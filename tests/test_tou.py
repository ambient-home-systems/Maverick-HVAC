"""Time of use: the peak window, billed cost, and recovering a KNOWN setback effect."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import synth
import analysis

FAILS = []
NY = ZoneInfo("America/New_York")


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def test_window():
    std = analysis.TOU("15:00", "20:00", True, "standard")
    jan = lambda h: std.is_peak(datetime(2026, 1, 7, h, tzinfo=NY))   # Wednesday, EST
    jul = lambda h: std.is_peak(datetime(2026, 7, 8, h, tzinfo=NY))   # Wednesday, EDT
    check("standard clock, winter = 15-19", [h for h in range(24) if jan(h)] == [15, 16, 17, 18, 19])
    check("standard clock, summer = 16-20", [h for h in range(24) if jul(h)] == [16, 17, 18, 19, 20])
    loc = analysis.TOU("16:00", "21:00", True, "local")
    check("local clock never moves", [h for h in range(24) if loc.is_peak(datetime(2026, 1, 7, h, tzinfo=NY))] == [16, 17, 18, 19, 20])
    check("weekend has no peak", not std.is_peak(datetime(2026, 1, 10, 16, tzinfo=NY)))
    check("every-day window", analysis.TOU("16:00", "21:00", False).is_peak(datetime(2026, 1, 10, 16, tzinfo=NY)))
    night = analysis.TOU("22:00", "06:00", False)
    check("window across midnight", night.is_peak(datetime(2026, 1, 7, 23, tzinfo=NY)) and night.is_peak(datetime(2026, 1, 7, 3, tzinfo=NY))
          and not night.is_peak(datetime(2026, 1, 7, 12, tzinfo=NY)))
    check("window hours on a weekend day (as if peak)", std.window_hours(date(2026, 1, 10), NY) == [15, 16, 17, 18, 19])


CUT, BACK = 0.35, 0.5   # the setback removes 35 % of peak-hour energy; half of it comes back in the 3 h after


def house_with_setback(setback_day):
    """synth.house in New York time; cooling days the setback_day() picks get the setback.
    Returns series, setpoints, t1, and the true per-day saving at the test rates."""
    series, t0, t1 = synth.house(days=540, start=datetime(2025, 1, 1, 5, tzinfo=timezone.utc))
    tou = analysis.TOU("15:00", "20:00", True, "standard", 0.21, 0.14)
    power, outdoor = series["power"], series["outdoor"]
    by_day = {}
    for ts in power:
        dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(NY)
        by_day.setdefault(dt.date(), {})[dt.hour] = ts
    sp, savings = {}, {}
    for d, hrs in by_day.items():
        win = tou.window_hours(d, NY)
        # the thermostat sets back whenever it is cooling through the peak window
        wt = [outdoor[hrs[h]]["mean"] for h in win if h in hrs]
        cooling = bool(wt) and max(wt) > synth.TRUE["tc"]
        on = cooling and d.weekday() < 5 and setback_day(d)
        for h, ts in hrs.items():
            sp[ts] = 74.0 if (on and h in win) else 72.0
        if not on:
            continue
        removed = 0.0
        for h in win:
            if h in hrs:
                p = power[hrs[h]]
                cut = p["mean"] * CUT
                p["mean"] -= cut
                removed += cut
        for h in range(win[-1] + 1, win[-1] + 4):
            if h in hrs:
                power[hrs[h]]["mean"] += removed * BACK / 3
        savings[d] = removed / 1000 * 0.21 - removed * BACK / 1000 * 0.14
    return series, {"cool": sp}, t1, tou, savings


def true_saving(series, tou, savings, labeled=None):
    """The true average saving over the days the analysis itself compares: weekdays it
    calls cooling days (or only the labeled setback days)."""
    s = analysis.Settings(NY)
    model = analysis.fit_model(analysis.build_days(series, s), s)
    days = [d for d in analysis._tou_days(series, s, tou, model) if d["weekday"] and d["mode"] == "cool"]
    if labeled is not None:
        days = [d for d in days if d["date"] in labeled]
    return sum(savings.get(d["date"], 0.0) for d in days) / len(days)


def test_setback_vs_weekends():
    series, sp, t1, tou, savings = house_with_setback(lambda d: True)
    res = analysis.analyze(series, analysis.Settings(NY), t1, tou=tou)  # no setpoints: weekend proxy
    true_saving_ = true_saving(series, tou, savings)
    c = res["tou"]["compare"]["cool"]
    check("weekend proxy used without setpoints", c["method"] == "weekends", c["method"])
    check("weekend proxy recovers the saving", abs(c["cost"] - true_saving_) < max(0.1, 2.5 * c["cost_se"]), (c["cost"], round(true_saving_, 3), c["cost_se"]))
    check("verdict: saves", c["verdict"] == "saves", c["verdict"])
    check("peak energy drop seen", c["peak_kwh"] > 0, c["peak_kwh"])


def test_setback_ab_labeled():
    # alternate weeks: setback on even ISO weeks, flat on odd ones
    series, sp, t1, tou, savings = house_with_setback(lambda d: d.isocalendar()[1] % 2 == 0)
    res = analysis.analyze(series, analysis.Settings(NY), t1, tou=tou, setpoints=sp)
    true_saving_ = true_saving(series, tou, savings, labeled=set(savings))
    c = res["tou"]["compare"]["cool"]
    check("A/B: setpoint labels used", c["method"] == "setpoints" and c["labeled_setback"] >= 5 and c["labeled_flat"] >= 5,
          (c["method"], c["labeled_setback"], c["labeled_flat"]))
    check("A/B recovers the saving", abs(c["cost"] - true_saving_) < max(0.1, 2.5 * c["cost_se"]), (c["cost"], round(true_saving_, 3), c["cost_se"]))
    check("A/B recovery energy seen", c["rec_kwh"] < 0, c["rec_kwh"])


def test_no_setback_no_effect():
    series, t0, t1 = synth.house(days=540, start=datetime(2025, 1, 1, 5, tzinfo=timezone.utc))
    tou = analysis.TOU("15:00", "20:00", True, "standard", 0.21, 0.14)
    res = analysis.analyze(series, analysis.Settings(NY), t1, tou=tou)
    c = res["tou"]["compare"]["cool"]
    check("no setback: no verdict either way", c["verdict"] in ("no clear difference", "not enough data"), (c["verdict"], c.get("cost"), c.get("cost_se")))


def test_billed_since():
    series, t0, t1 = synth.house(days=200, start=datetime(2025, 1, 1, 5, tzinfo=timezone.utc))
    tou = analysis.TOU("15:00", "20:00", True, "standard", 0.21, 0.14, since=date(2025, 5, 1))
    res = analysis.analyze(series, analysis.Settings(NY), t1, tou=tou)
    months = [m["month"] for m in res["tou"]["monthly"]]
    check("billed months start at tou_since", months and months[0] == "2025-05", months[:3])
    m = res["tou"]["monthly"][1]
    check("ceiling = peak kWh x spread", abs(m["ceiling"] - m["peak_kwh"] * 0.07) < 0.05, m)
    check("no TOU configured: section absent", analysis.analyze(series, analysis.Settings(NY), t1)["tou"] is None)


def run():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(f"-- {name}")
            fn()
    return FAILS
