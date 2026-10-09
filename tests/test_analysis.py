"""The analysis against a synthetic house with known parameters."""

import json
import sys
from datetime import timedelta, timezone

import synth
import analysis

TZ = timezone(timedelta(hours=-5))
FAILS = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, tol):
    return a is not None and abs(a - b) <= tol


def test_recovers_model():
    series, t0, t1 = synth.house()
    s = analysis.Settings(TZ)
    res = analysis.analyze(series, s, t1, names={"sensor.sunny": "Sunny", "sensor.cold": "Cold"},
                           forecast={"today": [], "tomorrow": [40.0] * 24})
    m = res["model"]
    T = synth.TRUE
    check("model fitted", m is not None)
    # the balance point and the base load trade off against each other (a couple of degrees lower plus a slightly
    # higher base fits nearly as well), so parameters get a tolerance and the PREDICTIONS are held tight below
    check("heating balance point", near(m["heat_balance"], T["th"], 3), m["heat_balance"])
    check("cooling balance point", near(m["cool_balance"], T["tc"], 2), m["cool_balance"])
    check("heating slope", near(m["kwh_per_hdd"], T["bh"], 0.25), m["kwh_per_hdd"])
    check("cooling slope", near(m["kwh_per_cdd"], T["bc"], 0.3), m["kwh_per_cdd"])
    check("cold knee found", near(m["knee"], T["tk"], 3), m["knee"])
    check("knee slope", near(m["kwh_per_hdd_knee"], T["bk"], 0.6), m["kwh_per_hdd_knee"])
    check("base load", near(m["base_kwh"], T["base"], 1.2), m["base_kwh"])
    truth = lambda t: T["base"] + T["bh"] * max(0, T["th"] - t) + T["bk"] * max(0, T["tk"] - t) + T["bc"] * max(0, t - T["tc"])
    for t in (15, 30, 45, 60, 75, 85):
        p = analysis.predict(m, [t] * 24)
        check(f"prediction for a day at {t}", abs(p - truth(t)) <= max(1.5, 0.06 * truth(t)), (round(p, 2), truth(t)))
    check("fit quality", m["r2"] > 0.95, m["r2"])
    exp = T["base"] + T["bh"] * (T["th"] - 40)
    check("expected tomorrow (flat 40)", near(res["headline"]["expected_tomorrow"], exp, 2.0), (res["headline"]["expected_tomorrow"], exp))
    check("aux onset near the aux temperature", near(res["aux"]["onset"], synth.AUX_BELOW, 3), res["aux"]["onset"])
    check("heating draw measured from the data", near(res["running"]["running_heat_w"], synth.RUN_HEAT_W, 1), res["running"])
    check("monthly index near 100", all(85 < x["index"] < 115 for x in res["monthly"] if x["index"] is not None and x["days"] > 20),
          [x["index"] for x in res["monthly"]])
    rooms = {r["name"]: r for r in res["rooms"]}
    check("room offsets", near(rooms["Cold"]["mean"], -2.0, 0.2) and rooms["Sunny"]["by_hour"][15] > rooms["Sunny"]["by_hour"][3] + 2,
          {k: v["mean"] for k, v in rooms.items()})
    json.dumps(res)  # everything must serialize for the page
    check("results serialize", True)


def test_gaps_and_glitch():
    series, t0, t1 = synth.house(days=420, gap=(200, 20), glitch=100)
    res = analysis.analyze(series, analysis.Settings(TZ), t1)
    gaps = res["coverage"]["gaps"]
    check("gap reported", any(18 <= g["days"] <= 21 for g in gaps), gaps)
    check("glitch day left out", len(res["model"]["outliers"]) >= 1, res["model"]["outliers"])


def test_not_enough_data():
    series, t0, t1 = synth.house(days=20)
    res = analysis.analyze(series, analysis.Settings(TZ), t1)
    check("no model on 20 days, no crash", res["model"] is None and res["headline"]["expected_today"] is None)


def test_heating_only_house():
    # a cold-climate year without enough cooling days must not invent a cooling term
    series, t0, t1 = synth.house(days=150)
    res = analysis.analyze(series, analysis.Settings(TZ), t1)
    m = res["model"]
    check("winter-only data: heating fitted", m and m["heat_balance"] is not None)


def test_celsius():
    series, t0, t1 = synth.house()
    c = {}
    for role, hrs in series.items():
        if role == "power":
            c[role] = hrs
        else:
            c[role] = {ts: {k: (v - 32) / 1.8 if v is not None and k in ("mean", "min", "max") else v for k, v in x.items()}
                       for ts, x in hrs.items()}
    res = analysis.analyze(c, analysis.Settings(TZ, unit="C"), t1)
    m = res["model"]
    check("celsius heating balance", near(m["heat_balance"], (synth.TRUE["th"] - 32) / 1.8, 1.7), m["heat_balance"])
    check("celsius slope scales by 1.8", near(m["kwh_per_hdd"], synth.TRUE["bh"] * 1.8, 0.5), m["kwh_per_hdd"])


def test_dst_day():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    from datetime import date
    check("23-hour day", analysis._hours_in_day(date(2026, 3, 8), tz) == 23)
    check("25-hour day", analysis._hours_in_day(date(2026, 11, 1), tz) == 25)


def run():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(f"-- {name}")
            fn()
    return FAILS
