"""Maverick HVAC - the analysis. Pure functions over hourly statistics; no I/O.

Everything here takes plain dicts and lists and returns plain dicts and lists, so
it can be exercised against synthetic houses (tests/) without Home Assistant.

Input: ``series`` maps a role to ``{hour_start_utc_seconds: {"mean", "min", "max"}}``.
Roles: ``power`` (W, the HVAC circuit), ``outdoor`` and ``indoor`` (temperature),
optionally ``duty`` (0-100 % compressor duty) and ``room:<entity_id>`` per room.

The core is the ASHRAE change-point model (Guideline 14 / PRISM): daily energy =
base + heating slope x HDD(Th) + cooling slope x CDD(Tc), with the two balance
points Th and Tc found by grid search. Degree-days come from the 24 hourly
outdoor readings (degree-hours / 24), not from (high + low) / 2, so a cold night
followed by a warm afternoon counts both ways - which is what a heat pump that
heats at 6 AM and cools at 4 PM actually does.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

DEFAULTS = {
    "unit": "F",
    "aux_threshold_w": 5000.0,      # circuit above this = electric strips on
    "heat_pump_running_w": 3500.0,  # typical heat pump draw while heating (aux estimate)
    "aux_mild_above": None,         # outdoor temp above which aux is "probably avoidable"
    "model_days": 365,              # train on the most recent N complete days
    "room_days": 30,                # room deviation window
    "recent_days": 30,              # "last 30 days" summaries
}


class Settings:
    def __init__(self, tz, **kw):
        cfg = dict(DEFAULTS)
        cfg.update({k: v for k, v in kw.items() if v is not None})
        self.tz = tz
        self.unit = cfg["unit"]
        f = self.unit == "F"
        self.aux_threshold_w = float(cfg["aux_threshold_w"])
        self.heat_pump_running_w = float(cfg["heat_pump_running_w"])
        self.aux_mild_above = float(cfg["aux_mild_above"]) if cfg["aux_mild_above"] is not None else (40.0 if f else 4.5)
        self.model_days = int(cfg["model_days"])
        self.room_days = int(cfg["room_days"])
        self.recent_days = int(cfg["recent_days"])
        # balance-point search ranges and steps, in the configured unit
        self.heat_bases = _frange(40, 72, 1) if f else _frange(4, 22, 0.5)
        self.cool_bases = _frange(55, 82, 1) if f else _frange(13, 28, 0.5)
        self.knee_bases = _frange(5, 50, 1) if f else _frange(-15, 10, 0.5)
        self.bin_width = 2.0 if f else 1.0
        self.min_coast_delta = 8.0 if f else 4.5
        # hours whose circuit PEAK stays under this had no compressor minute at all
        self.no_run_max_w = 1000.0


def _frange(a, b, step):
    out, x = [], float(a)
    while x <= b + 1e-9:
        out.append(round(x, 3))
        x += step
    return out


# ----------------------------------------------------------------------------
# Small numerics (stdlib only)
# ----------------------------------------------------------------------------

def lstsq(rows, y):
    """Least squares via the normal equations - fine for 1-3 well-scaled columns."""
    k = len(rows[0])
    a = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for r, t in zip(rows, y):
        for i in range(k):
            b[i] += r[i] * t
            ri = r[i]
            for j in range(k):
                a[i][j] += ri * r[j]
    return _solve(a, b)


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for j in range(c, n + 1):
                    m[r][j] -= f * m[c][j]
    return [m[i][n] / m[i][i] for i in range(n)]


def median(xs):
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _r(x, nd=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, nd)


# ----------------------------------------------------------------------------
# Hours and days
# ----------------------------------------------------------------------------

def _local_day(ts, tz):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz).date()


def _hours_in_day(d, tz):
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    nxt = start + timedelta(days=1)
    nxt = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return round((nxt.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600)


def build_days(series, s):
    """Group hourly statistics into local days. A day is complete when it has
    nearly every hour of both power and outdoor temperature."""
    power, outdoor, indoor = series.get("power", {}), series.get("outdoor", {}), series.get("indoor", {})
    by_day = {}
    for ts in set(power) | set(outdoor):
        by_day.setdefault(_local_day(ts, s.tz), []).append(ts)
    days = []
    for d in sorted(by_day):
        hrs = sorted(by_day[d])
        p = [power[t] for t in hrs if t in power and power[t].get("mean") is not None]
        temps = [outdoor[t]["mean"] for t in hrs if t in outdoor and outdoor[t].get("mean") is not None]
        ins = [indoor[t]["mean"] for t in hrs if t in indoor and indoor[t].get("mean") is not None]
        want = _hours_in_day(d, s.tz)
        complete = len(p) >= want - 2 and len(temps) >= want - 4
        kwh = sum(x["mean"] for x in p) / 1000.0 * (want / len(p)) if p else None
        aux_h, aux_kwh = 0, 0.0
        for x in p:
            if (x.get("max") or 0) > s.aux_threshold_w:
                aux_h += 1
                aux_kwh += max(0.0, x["mean"] - s.heat_pump_running_w) / 1000.0
        days.append({
            "date": d, "complete": complete, "n_power": len(p), "temps": temps,
            "kwh": kwh, "aux_hours": aux_h, "aux_kwh": aux_kwh,
            "t_mean": sum(temps) / len(temps) if temps else None,
            "t_min": min(temps) if temps else None, "t_max": max(temps) if temps else None,
            "indoor": sum(ins) / len(ins) if ins else None,
        })
    return days


def degree(temps, base, heating):
    if not temps:
        return 0.0
    if heating:
        return sum(max(0.0, base - t) for t in temps) / len(temps)
    return sum(max(0.0, t - base) for t in temps) / len(temps)


# ----------------------------------------------------------------------------
# Change-point model
# ----------------------------------------------------------------------------

def fit_model(days, s):
    """Grid-search the heating and cooling balance points; least squares for the
    rest. Slopes are forced non-negative (a term that fits negative is dropped),
    and a season with too few days to estimate is left out rather than guessed.

    Then a COLD KNEE: a second heating term HDD(Tk) below a lower base. A heat
    pump's cost per degree climbs in deep cold - aux strips come in and the
    compressor's COP falls - and a straight line misses those days by 30 % or
    more. Only kept when the extra slope is positive and it cuts the squared
    error by 3 %.

    One outlier pass at the end drops days the full model misses by > 3.5 RMSE
    (the system off for a trip, a power-monitor glitch) and refits - never a day
    that ran aux."""
    train = [d for d in days if d["complete"] and d["kwh"] is not None and d["temps"]]
    train = train[-s.model_days:]
    if len(train) < 30:
        return None
    y = [d["kwh"] for d in train]
    hdd = {b: [degree(d["temps"], b, True) for d in train] for b in sorted(set(s.heat_bases) | set(s.knee_bases))}
    cdd = {b: [degree(d["temps"], b, False) for d in train] for b in s.cool_bases}
    enough_h = lambda b, idx: sum(1 for i in idx if hdd[b][i] > 1.0) >= 10
    enough_c = lambda b, idx: sum(1 for i in idx if cdd[b][i] > 1.0) >= 10

    def terms(m, i):
        r = [1.0]
        if m["th"] is not None:
            r.append(hdd[m["th"]][i])
        if m["tk"] is not None:
            r.append(hdd[m["tk"]][i])
        if m["tc"] is not None:
            r.append(cdd[m["tc"]][i])
        return r

    def fit(th, tk, tc, idx):
        m = {"th": th, "tk": tk, "tc": tc}
        sol = lstsq([terms(m, i) for i in idx], [y[i] for i in idx])
        if sol is None or any(v <= 0 for v in sol[1:]):
            return None
        k = 1
        m["base"] = sol[0]
        m["bh"] = sol[k] if th is not None else 0.0
        k += th is not None
        m["bk"] = sol[k] if tk is not None else 0.0
        k += tk is not None
        m["bc"] = sol[k] if tc is not None else 0.0
        m["sse"] = sum((y[i] - sum(c * t for c, t in zip(sol, terms(m, i)))) ** 2 for i in idx)
        return m

    def search(idx):
        best = None
        cands = [(th, None, tc) for th in s.heat_bases if enough_h(th, idx) for tc in s.cool_bases
                 if tc >= th and enough_c(tc, idx)]
        cands += [(th, None, None) for th in s.heat_bases if enough_h(th, idx)]
        cands += [(None, None, tc) for tc in s.cool_bases if enough_c(tc, idx)]
        for th, tk, tc in cands:
            m = fit(th, tk, tc, idx)
            if m and (best is None or m["sse"] < best["sse"]):
                best = m
        if best and best["th"] is not None:
            # the knee moves the balance point too (the single slope was averaging the two), so search both
            gap = 4.0 if s.unit == "F" else 2.0
            knee = None
            for th in s.heat_bases:
                if not enough_h(th, idx):
                    continue
                for tk in s.knee_bases:
                    if tk > th - gap or not enough_h(tk, idx):
                        continue
                    m = fit(th, tk, best["tc"], idx)
                    if m and (knee is None or m["sse"] < knee["sse"]):
                        knee = m
            if knee and knee["sse"] < 0.97 * best["sse"]:
                best = knee
        return best

    def pred(m, i):
        return m["base"] + (m["bh"] * hdd[m["th"]][i] if m["th"] is not None else 0) + \
            (m["bk"] * hdd[m["tk"]][i] if m["tk"] is not None else 0) + (m["bc"] * cdd[m["tc"]][i] if m["tc"] is not None else 0)

    idx = list(range(len(train)))
    best = search(idx)
    if best is None:
        return None
    rmse = math.sqrt(best["sse"] / len(idx))
    # aux days are never outliers: a cold snap that ran the strips all day is the most interesting day in the set
    keep = [i for i in idx if train[i]["aux_hours"] > 0 or abs(y[i] - pred(best, i)) <= 3.5 * rmse]
    kept = set(keep)
    outliers = [train[i]["date"].isoformat() for i in idx if i not in kept]
    if len(keep) < len(idx):
        best = search(keep) or best
    n = len(keep)
    ys = [y[i] for i in keep]
    mean = sum(ys) / n
    sst = sum((v - mean) ** 2 for v in ys)
    sse = sum((y[i] - pred(best, i)) ** 2 for i in keep)
    rmse = math.sqrt(sse / n)
    return {
        "base_kwh": best["base"], "kwh_per_hdd": best["bh"] or None, "kwh_per_cdd": best["bc"] or None,
        "heat_balance": best["th"], "cool_balance": best["tc"],
        "knee": best["tk"], "kwh_per_hdd_knee": best["bk"] or None,
        "r2": 1 - sse / sst if sst > 0 else None, "rmse": rmse, "cv_rmse": rmse / mean if mean else None,
        "n_days": n, "from": train[0]["date"].isoformat(), "to": train[-1]["date"].isoformat(),
        "outliers": outliers,
    }


def predict(model, temps):
    """Expected kWh for a day with these hourly outdoor temperatures."""
    if not model or not temps:
        return None
    h, c = split(model, temps)
    return model["base_kwh"] + h + c


def split(model, temps):
    """(heating kWh, cooling kWh) parts of a prediction, without the base."""
    if not model or not temps:
        return 0.0, 0.0
    h = c = 0.0
    if model["heat_balance"] is not None:
        h += model["kwh_per_hdd"] * degree(temps, model["heat_balance"], True)
    if model.get("knee") is not None:
        h += model["kwh_per_hdd_knee"] * degree(temps, model["knee"], True)
    if model["cool_balance"] is not None:
        c += model["kwh_per_cdd"] * degree(temps, model["cool_balance"], False)
    return h, c


# ----------------------------------------------------------------------------
# Hourly duty, capacity and aux
# ----------------------------------------------------------------------------

def hourly(series, s, model):
    """Per-hour records with an estimated compressor duty (0-1).

    With a duty sensor (``duty`` role, 0-100 %) that is used directly. Otherwise
    duty = (mean - idle) / (running - idle), where running is the typical draw in
    that mode, measured from the data as the median hourly PEAK on clearly-heating
    or clearly-cooling hours. Hours whose peak crosses the aux threshold count as
    duty 1 and are flagged. Blower-only ventilation inflates the estimate
    slightly; a duty sensor removes that."""
    power, outdoor, duty = series.get("power", {}), series.get("outdoor", {}), series.get("duty", {})
    th = model["heat_balance"] if model and model["heat_balance"] is not None else (60.0 if s.unit == "F" else 15.5)
    tc = model["cool_balance"] if model and model["cool_balance"] is not None else (70.0 if s.unit == "F" else 21.0)
    margin = 8.0 if s.unit == "F" else 4.5
    peaks_h, peaks_c, idle = [], [], []
    for ts, p in power.items():
        o = outdoor.get(ts)
        if not o or p.get("max") is None or o.get("mean") is None:
            continue
        mx = p["max"]
        if mx < s.no_run_max_w:
            idle.append(p["mean"])
        elif mx < s.aux_threshold_w:
            if o["mean"] < th - margin:
                peaks_h.append(mx)
            elif o["mean"] > tc + margin:
                peaks_c.append(mx)
    run_h = median(peaks_h) or s.heat_pump_running_w
    run_c = median(peaks_c) or s.heat_pump_running_w * 0.75
    idle_w = median(idle) or 30.0
    out = []
    mid = (th + tc) / 2
    for ts in sorted(power):
        p, o = power[ts], outdoor.get(ts)
        if not o or p.get("mean") is None or o.get("mean") is None:
            continue
        t = o["mean"]
        heat = t < mid
        aux = (p.get("max") or 0) > s.aux_threshold_w
        if ts in duty and duty[ts].get("mean") is not None:
            dty, measured = max(0.0, min(1.0, duty[ts]["mean"] / 100.0)), True
        elif aux:
            dty, measured = 1.0, False
        else:
            run = run_h if heat else run_c
            dty = max(0.0, min(1.0, (p["mean"] - idle_w) / max(1.0, run - idle_w)))
            measured = False
        out.append({"ts": ts, "t": t, "mean": p["mean"], "max": p.get("max"), "heat": heat, "aux": aux,
                    "duty": dty, "measured": measured})
    return out, {"running_heat_w": run_h, "running_cool_w": run_c, "idle_w": idle_w}


def duty_curve(hours, s):
    bins = {}
    for h in hours:
        b = math.floor(h["t"] / s.bin_width) * s.bin_width
        x = bins.setdefault(b, {"n": 0, "duty": 0.0, "aux": 0, "heat": 0})
        x["n"] += 1
        x["duty"] += h["duty"]
        x["aux"] += 1 if h["aux"] else 0
        x["heat"] += 1 if h["heat"] else 0
    return [{"t": b + s.bin_width / 2, "n": x["n"], "duty": x["duty"] / x["n"], "aux_frac": x["aux"] / x["n"],
             "heat": x["heat"] * 2 >= x["n"]} for b, x in sorted(bins.items()) if x["n"] >= 6]


def capacity(hours, s, model):
    """Outdoor temperature where the fitted duty line reaches 100 % - heating and
    cooling. Uses hours well past each balance point, aux hours counting as 1."""
    res = {}
    if not model:
        return res
    m = 3.0 if s.unit == "F" else 1.5
    for key, sel, sign in (("heat", lambda h: model["heat_balance"] is not None and h["t"] < model["heat_balance"] - m, -1),
                           ("cool", lambda h: model["cool_balance"] is not None and h["t"] > model["cool_balance"] + m, 1)):
        pts = [h for h in hours if sel(h)]
        if len(pts) < 48:
            continue
        sol = lstsq([[1.0, h["t"]] for h in pts], [h["duty"] for h in pts])
        if not sol or sol[1] * sign <= 0:
            continue
        t100 = (1.0 - sol[0]) / sol[1]
        lo, hi = (-40, 60) if s.unit == "F" else (-40, 16)
        if key == "cool":
            lo, hi = (70, 130) if s.unit == "F" else (21, 55)
        if lo <= t100 <= hi:
            res[key] = {"t100": t100, "intercept": sol[0], "slope": sol[1], "n": len(pts)}
    return res


def aux_summary(hours, days, s, model, now_ts):
    recent_cut = now_ts - s.recent_days * 86400
    year_cut = now_ts - 365 * 86400
    curve = duty_curve([h for h in hours if h["heat"]], s)
    onset = None
    for b in curve:
        if b["aux_frac"] >= 0.10:
            onset = b["t"] if onset is None else max(onset, b["t"])
    mild = [h for h in hours if h["aux"] and h["t"] >= s.aux_mild_above]

    def agg(cut):
        hs = [h for h in hours if h["ts"] >= cut]
        aux_h = [h for h in hs if h["aux"]]
        aux_kwh = sum(max(0.0, h["mean"] - s.heat_pump_running_w) for h in aux_h) / 1000.0
        heat_kwh = sum(h["mean"] for h in hs if h["heat"]) / 1000.0
        mild_h = [h for h in aux_h if h["t"] >= s.aux_mild_above]
        return {"aux_hours": len(aux_h), "aux_kwh": aux_kwh, "heating_kwh": heat_kwh,
                "aux_share": aux_kwh / heat_kwh if heat_kwh > 1 else None,
                "mild_hours": len(mild_h), "mild_kwh": sum(max(0.0, h["mean"] - s.heat_pump_running_w) for h in mild_h) / 1000.0}

    events = [{"ts": h["ts"], "t": _r(h["t"], 1), "peak_w": _r(h["max"], 0),
               "aux_kwh": _r(max(0.0, h["mean"] - s.heat_pump_running_w) / 1000.0, 2)} for h in mild[-25:]][::-1]
    return {"onset": onset, "recent": agg(recent_cut), "year": agg(year_cut), "mild_events": events}


# ----------------------------------------------------------------------------
# House time constant (coast-down)
# ----------------------------------------------------------------------------

def coast(series, s):
    """How fast the house drifts toward outdoor with the system off: night hours
    where neither this hour nor the next ran the compressor. dIndoor/dt = -k x
    (indoor - outdoor); the time constant is 1/k hours. Hourly means of a
    thermostat that reports whole degrees make each sample noisy - the fit over
    many nights is what carries the information."""
    power, outdoor, indoor = series.get("power", {}), series.get("outdoor", {}), series.get("indoor", {})
    xs, ys = [], []
    for ts in sorted(indoor):
        nx = ts + 3600
        if nx not in indoor or ts not in outdoor or ts not in power or nx not in power:
            continue
        hr = datetime.fromtimestamp(ts, timezone.utc).astimezone(s.tz).hour
        if not (hr >= 21 or hr <= 4):
            continue
        if (power[ts].get("max") or 0) >= s.no_run_max_w or (power[nx].get("max") or 0) >= s.no_run_max_w:
            continue
        dt = indoor[ts]["mean"] - outdoor[ts]["mean"]
        if abs(dt) < s.min_coast_delta:
            continue
        xs.append(dt)
        ys.append(indoor[nx]["mean"] - indoor[ts]["mean"])
    if len(xs) < 40:
        return {"n": len(xs), "tau_h": None}
    sxx = sum(x * x for x in xs)
    k = -sum(x * y for x, y in zip(xs, ys)) / sxx
    return {"n": len(xs), "k": k, "tau_h": 1.0 / k if k > 0 else None,
            "points": [[_r(x, 1), _r(y, 2)] for x, y in zip(xs[-400:], ys[-400:])]}


# ----------------------------------------------------------------------------
# Rooms
# ----------------------------------------------------------------------------

def rooms(series, s, names, now_ts):
    """Each room against the thermostat, by local hour of day, over the last
    room_days days: where the house is uneven, and when (sun, occupancy)."""
    indoor = series.get("indoor", {})
    cut = now_ts - s.room_days * 86400
    out = []
    for role, hrs in series.items():
        if not role.startswith("room:"):
            continue
        ent = role[5:]
        by_hour = [[] for _ in range(24)]
        for ts, v in hrs.items():
            if ts < cut or ts not in indoor or v.get("mean") is None:
                continue
            hr = datetime.fromtimestamp(ts, timezone.utc).astimezone(s.tz).hour
            by_hour[hr].append(v["mean"] - indoor[ts]["mean"])
        allv = [x for h in by_hour for x in h]
        if len(allv) < 48:
            continue
        hours = [_r(sum(h) / len(h), 2) if h else None for h in by_hour]
        vals = [x for x in hours if x is not None]
        out.append({"entity": ent, "name": names.get(ent, ent), "mean": _r(sum(allv) / len(allv), 2),
                    "by_hour": hours, "swing": _r(max(vals) - min(vals), 2) if vals else None, "n": len(allv)})
    out.sort(key=lambda r: -abs(r["mean"] or 0))
    return out


# ----------------------------------------------------------------------------
# Everything
# ----------------------------------------------------------------------------

def analyze(series, s, now_ts, names=None, forecast=None):
    """Run every analysis. ``forecast`` = {"today": [temps for the remaining hours],
    "tomorrow": [24 temps]}; today's past hours come from the outdoor series."""
    days = build_days(series, s)
    model = fit_model(days, s)
    hours, run = hourly(series, s, model)
    complete = [d for d in days if d["complete"]]
    gaps = [{"from": (a["date"] + timedelta(days=1)).isoformat(), "to": (b["date"] - timedelta(days=1)).isoformat(),
             "days": (b["date"] - a["date"]).days - 1} for a, b in zip(complete, complete[1:]) if (b["date"] - a["date"]).days > 3]
    daily = []
    for d in complete[-400:]:
        e = predict(model, d["temps"])
        h, c = split(model, d["temps"])
        daily.append({"date": d["date"].isoformat(), "kwh": _r(d["kwh"]), "expected": _r(e),
                      "aux_kwh": _r(d["aux_kwh"]), "aux_hours": d["aux_hours"],
                      "t_mean": _r(d["t_mean"], 1), "t_min": _r(d["t_min"], 1), "t_max": _r(d["t_max"], 1),
                      "mode": "heat" if h > c else ("cool" if c > h else "none")})
    months = {}
    for d in complete:
        e = predict(model, d["temps"])
        if e is None:
            continue
        m = months.setdefault(d["date"].strftime("%Y-%m"), {"actual": 0.0, "expected": 0.0, "days": 0, "aux": 0.0})
        m["actual"] += d["kwh"]
        m["expected"] += e
        m["days"] += 1
        m["aux"] += d["aux_kwh"]
    monthly = [{"month": k, "actual": _r(v["actual"], 1), "expected": _r(v["expected"], 1), "days": v["days"],
                "aux_kwh": _r(v["aux"], 1), "index": _r(100 * v["actual"] / v["expected"], 1) if v["expected"] > 5 else None}
               for k, v in sorted(months.items())]

    today = datetime.fromtimestamp(now_ts, timezone.utc).astimezone(s.tz).date()
    yday = today - timedelta(days=1)
    yrec = next((d for d in days if d["date"] == yday and d["complete"]), None)
    exp_y = predict(model, yrec["temps"]) if yrec else None
    outdoor = series.get("outdoor", {})
    past_today = [outdoor[t]["mean"] for t in sorted(outdoor) if _local_day(t, s.tz) == today and outdoor[t].get("mean") is not None]
    fc = forecast or {}
    today_temps = past_today + list(fc.get("today") or [])
    exp_today = predict(model, today_temps) if len(today_temps) >= 20 else None
    exp_tomorrow = predict(model, fc.get("tomorrow")) if len(fc.get("tomorrow") or []) >= 20 else None
    cap = capacity(hours, s, model)
    aux = aux_summary(hours, days, s, model, now_ts)
    recent = [d for d in complete if d["date"] > today - timedelta(days=s.recent_days)]
    r_act = sum(d["kwh"] for d in recent)
    r_exp = sum(predict(model, d["temps"]) or 0 for d in recent)

    return {
        "generated": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
        "unit": s.unit,
        "coverage": {"first": days[0]["date"].isoformat() if days else None, "last": days[-1]["date"].isoformat() if days else None,
                     "days": len(days), "complete_days": len(complete), "hours": len(hours), "gaps": gaps,
                     "measured_duty_hours": sum(1 for h in hours if h["measured"])},
        "model": {k: (_r(v, 3) if isinstance(v, float) else v) for k, v in model.items()} if model else None,
        "running": {k: _r(v, 0) for k, v in run.items()},
        "headline": {
            "expected_today": _r(exp_today), "expected_tomorrow": _r(exp_tomorrow),
            "yesterday_actual": _r(yrec["kwh"]) if yrec else None, "yesterday_expected": _r(exp_y),
            "yesterday_index": _r(100 * yrec["kwh"] / exp_y, 1) if yrec and exp_y and exp_y > 1 else None,
            "recent_index": _r(100 * r_act / r_exp, 1) if r_exp > 5 else None,
            "recent_actual": _r(r_act, 1), "recent_expected": _r(r_exp, 1),
            "heat_capacity_temp": _r(cap["heat"]["t100"], 1) if "heat" in cap else None,
            "cool_capacity_temp": _r(cap["cool"]["t100"], 1) if "cool" in cap else None,
            "aux_onset_temp": _r(aux["onset"], 1),
            "aux_share_recent": _r(100 * aux["recent"]["aux_share"], 1) if aux["recent"]["aux_share"] is not None else None,
            "aux_share_year": _r(100 * aux["year"]["aux_share"], 1) if aux["year"]["aux_share"] is not None else None,
            "aux_hours_recent": aux["recent"]["aux_hours"], "aux_kwh_recent": _r(aux["recent"]["aux_kwh"], 1),
            "aux_mild_hours_recent": aux["recent"]["mild_hours"], "aux_mild_hours_year": aux["year"]["mild_hours"],
            "aux_mild_kwh_year": _r(aux["year"]["mild_kwh"], 1),
        },
        "daily": daily,
        "monthly": monthly,
        "duty_curve": [{k: (_r(v, 3) if isinstance(v, float) else v) for k, v in b.items()} for b in duty_curve(hours, s)],
        "capacity": {k: {kk: _r(vv, 4) for kk, vv in v.items()} for k, v in cap.items()},
        "aux": {"onset": _r(aux["onset"], 1), "mild_above": s.aux_mild_above,
                "recent": {k: _r(v, 3) if isinstance(v, float) else v for k, v in aux["recent"].items()},
                "year": {k: _r(v, 3) if isinstance(v, float) else v for k, v in aux["year"].items()},
                "mild_events": aux["mild_events"]},
        "coast": coast(series, s),
        "rooms": rooms(series, s, names or {}, now_ts),
    }
