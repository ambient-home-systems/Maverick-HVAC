"""A synthetic house with known answers.

Hourly outdoor temperature = seasonal + daily cycle + weather noise; HVAC energy
for each hour comes from a known change-point model applied to that hour, so the
fitted model should recover the parameters. Cold hours below AUX_BELOW run the
strips (circuit peak above the threshold), and a few deliberate gaps and a
glitch day exercise coverage and outliers.
"""

import math
import random
from datetime import datetime, timedelta, timezone

TRUE = {"base": 3.0, "bh": 1.5, "th": 52.0, "bk": 2.0, "tk": 34.0, "bc": 2.5, "tc": 67.0}
AUX_BELOW = 30.0
RUN_HEAT_W, RUN_COOL_W, IDLE_W = 3600.0, 2800.0, 40.0


def house(days=540, seed=7, start=datetime(2025, 1, 1, tzinfo=timezone.utc), gap=None, glitch=None, tz_offset_h=-5):
    """Returns (series, start_ts, end_ts). gap = (first_day, n_days) with no power;
    glitch = day index whose power is zero (system off)."""
    rnd = random.Random(seed)
    power, outdoor, indoor, rooms = {}, {}, {}, {"room:sensor.sunny": {}, "room:sensor.cold": {}}
    t0 = int(start.timestamp())
    weather = 0.0
    for h in range(days * 24):
        ts = t0 + h * 3600
        local = datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=tz_offset_h)
        doy = local.timetuple().tm_yday
        if h % 24 == 0:
            weather = 0.7 * weather + rnd.gauss(0, 6)
        t = 55 - 22 * math.cos(2 * math.pi * (doy - 15) / 365) + 9 * math.sin(2 * math.pi * (local.hour - 9) / 24) + weather
        # energy for this hour (kWh) from the true model applied hourly (degree-hours / 24 per hour)
        e = TRUE["base"] / 24 + TRUE["bh"] * max(0, TRUE["th"] - t) / 24 + TRUE["bk"] * max(0, TRUE["tk"] - t) / 24 \
            + TRUE["bc"] * max(0, t - TRUE["tc"]) / 24
        e *= 1 + rnd.gauss(0, 0.05)
        mean_w = max(IDLE_W, e * 1000)
        aux = t < AUX_BELOW
        peak = 9000.0 + rnd.random() * 4000 if aux else (RUN_HEAT_W if t < 60 else RUN_COOL_W) if mean_w > 150 else IDLE_W * 2
        day = h // 24
        if gap and gap[0] <= day < gap[0] + gap[1]:
            pass
        else:
            if glitch is not None and day == glitch:
                mean_w, peak = 0.0, 0.0
            power[ts] = {"mean": mean_w, "min": IDLE_W, "max": max(peak, mean_w)}
        outdoor[ts] = {"mean": t, "min": t - 1, "max": t + 1}
        ins = 70 + rnd.gauss(0, 0.3)
        indoor[ts] = {"mean": ins, "min": ins, "max": ins}
        rooms["room:sensor.sunny"][ts] = {"mean": ins + (3.0 if 13 <= local.hour <= 17 else 0.5)}
        rooms["room:sensor.cold"][ts] = {"mean": ins - 2.0}
    series = {"power": power, "outdoor": outdoor, "indoor": indoor}
    series.update(rooms)
    return series, t0, t0 + days * 86400
