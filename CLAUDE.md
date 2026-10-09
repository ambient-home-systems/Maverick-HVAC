# CLAUDE.md

Context for working on this repository.

## What this is

A Home Assistant add-on repository with one add-on, `maverick_hvac/`: HVAC /
heat pump efficiency analysis from Home Assistant's long-term statistics.

- `analysis.py` - every analysis, pure functions over hourly statistics, no I/O.
  Testable without Home Assistant.
- `maverick.py` - the service: reads statistics + forecast over the websocket
  API, runs `analysis.analyze()`, publishes headline entities over MQTT
  discovery, serves `ui.html` and `results` over ingress.
- `ui.html` - the results page. One file, hand-drawn SVG charts, no external
  requests.

## Hard constraints

- **Long-term statistics, not raw history.** Raw history is purged (10 days by
  default); statistics are kept forever and are hourly, which is the resolution
  everything here is designed for. Don't add a raw-history path to get finer
  data without raising it as a decision.
- **Standard library + websocket-client + paho-mqtt.** No numpy/pandas: the math
  is a few small least-squares fits and would cost a slow build on a Pi. If a
  future analysis truly needs numpy, raise it.
- **The page stays one file with no external requests** (ingress, offline
  installs). Every URL it uses is **relative** - ingress serves it under a
  per-session path and a leading slash escapes to Home Assistant's API.
- **Real house data never enters the repo** - not as fixtures, not as examples.
  Tests use `tests/synth.py`.
- **Bump `version` in `maverick_hvac/config.yaml` on every pushed change.**
  Supervisor compares nothing else.
- **No GitHub Actions** (Actions minutes are constrained on this account).
- **MQTT identity is fixed:** unique IDs `maverick_hvac_<key>` and the topics
  under `maverick_hvac/`. Renaming a key orphans the user's entity.

## Decisions already settled

- **Change-point model on daily energy vs degree-days from hourly
  temperatures** (ASHRAE Guideline 14 / PRISM). Balance points are searched.
- **Cold knee**: a second heating slope below a lower base, searched jointly with
  the heating balance point (the single slope averages the two, so fixing the
  balance point first lands it wrong). Kept only when it cuts squared error by
  3 %. On the first real house (2026-10) it landed at 35 °F - the same place the
  hourly aux onset did, independently.
- **Aux days are never outliers.** The outlier pass (> 3.5 RMSE) exists for
  glitches and systems switched off; a cold snap that ran the strips all day is
  real and is exactly what the knee is for.
- **Aux is read from power** (hourly peak above a threshold), because thermostats
  over HomeKit and most integrations don't report aux stages.
- **Run time is estimated from power unless a duty sensor exists**: (mean - idle)
  / (running - idle), with running = the median hourly peak on clearly-heating or
  clearly-cooling hours, measured from the data.
- **Balance point vs base load trade off** in any such fit; tests hold
  predictions tight and parameters loosely (tests/README.md).

## Conventions

- `maverick_hvac/DOCS.md` is the Documentation tab; root `README.md` is for
  GitHub visitors; `CHANGELOG.md` gets an entry per version.
- Run `python3 tests/run.py` before pushing a change to `analysis.py`,
  `maverick.py` or `ui.html`. Nothing runs it for you.
