# Tests

```
python3 tests/run.py
```

Standard library only. Nothing here ships: `tests/` is outside the add-on's build
context, so adding a check never needs a `version` bump.

- `synth.py` - a synthetic house: hourly outdoor temperature (season, daily cycle,
  weather), HVAC energy from a KNOWN change-point model applied hour by hour, aux
  below 30 °F, two room sensors with known offsets, optional gaps and a glitch day.
- `test_analysis.py` - the model recovers the house (parameters with tolerances,
  predictions held tight), gaps are reported, glitch days drop out, too little
  data degrades without crashing, Celsius, DST day lengths.
- `test_tou.py` - the peak window (standard vs local clock across DST, weekends,
  across midnight), billed months from `tou_since`, and a KNOWN setback (35 % of
  peak energy cut, half of it back in the recovery) recovered both ways: weekdays
  vs weekends, and an alternate-week A/B labeled from synthetic setpoints. No
  setback must give no verdict. The A/B house also warms one room 1.5 degrees by
  the end of each setback peak; the room comparison must find it, and find
  nothing in the room left alone.
- `test_service.py` - every MQTT entity has a state key, the page's endpoints, and
  the page uses only relative URLs (ingress).

The balance point and the base load trade off against each other in any
change-point fit - two degrees lower plus a slightly larger base fits almost as
well - so parameter checks carry a tolerance and the predictions are what is
held tight.

Real house data never goes in here (or anywhere in the repo).
