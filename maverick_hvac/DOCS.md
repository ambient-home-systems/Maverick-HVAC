# Maverick HVAC

Turns the history Home Assistant already keeps into answers about your heating
and cooling: how much energy the weather *should* have cost, whether the system
is drifting, where the heat pump runs out of capacity, how often the electric
strips run and whether they needed to, and which rooms run warm or cold.

It reads **long-term statistics** - the hourly mean / min / max Home Assistant
keeps forever for any sensor with a `state_class` - so it works on day one with
whatever history you have, not only from the day you install it.

## What you need

| Option | What to point it at |
|---|---|
| `power_entity` | The HVAC system's power in watts: one circuit that carries both the outdoor unit and the air handler (an Emporia Vue, Sense, IoTaWatt or similar), or a template sensor that adds two. |
| `outdoor_temperature_entity` | A local outdoor temperature sensor. |
| `indoor_temperature_entity` | The thermostat's temperature. |
| `weather_entity` | Optional. A weather entity with an hourly forecast, for "expected today / tomorrow". |
| `compressor_duty_entity` | Optional. A sensor that reads 100 while the compressor runs and 0 otherwise. With it, run time is measured; without it, it's estimated from power. |
| `rooms` | Optional. Room temperature sensors to compare against the thermostat. |

All of them need long-term statistics. A sensor shows up in
**Developer tools → Statistics** if it has them.

## What it works out

**The model.** Daily HVAC energy is fitted against degree-days - an ASHRAE
change-point model:

```
kWh a day = base + a × HDD(heating balance) + k × HDD(cold knee) + c × CDD(cooling balance)
```

- **Balance points** - the outdoor temperatures where heating and cooling start.
  Found by searching, not assumed to be 65 °F.
- **Cold knee** - a second, steeper heating slope below a lower temperature. A
  heat pump's cost per degree climbs in deep cold (aux strips, falling COP); a
  single straight line misses those days badly. Only used when the data shows it.
- **Base load** - energy that doesn't depend on weather (standby, ventilation).

Degree-days come from all 24 hourly outdoor readings, so a cold night and a warm
afternoon both count.

**Efficiency index.** Actual energy ÷ what the model expects for the weather that
happened. 100 % is typical for your house. Watch the monthly trend: a slow climb
is how a dirty filter, low refrigerant or a duct leak shows up.

**Run time vs outdoor temperature.** How much of each hour the system runs, by
outdoor temperature, and where the trend reaches 100 % - the heat pump's
capacity limit (the temperature below which it can't keep up alone).

**Aux heat.** Electric strips are read from power: any hour whose circuit peak
passes `aux_threshold_w`. Reported: hours, estimated energy, share of heating,
the temperature where aux starts showing up, and **aux above
`aux_mild_above`** - aux in mild weather, usually a setback recovery, which a
smaller setback or the thermostat's aux lockout setting avoids.

**Rooms.** Each room minus the thermostat, by hour of day over the last 30
days. Afternoon bumps are usually sun.

**House time constant.** On nights with the system off, how fast the house drifts
toward the outdoor temperature. Larger is a tighter house. Noisy hour to hour;
useful as a trend.

## Time of use

Set `tou_peak_start` and `tou_peak_end` to turn this section on.

- **What the peak costs.** HVAC energy and cost by peak / off-peak, per month and
  for the last 30 days, from `tou_since` (when TOU billing began).
- **The ceiling.** What the last 30 days would have saved if *every* peak kWh had
  moved to off-peak: peak kWh × (peak rate − off-peak rate). Usually small - it
  is the most any setpoint strategy could save.
- **Does a peak setback pay?** Per season (cooling, heating), days with the
  setback are compared with days without it **in the same weather**: days are
  grouped by the model's expected energy and compared within each group. It
  reports the difference in cost (at today's rates), total energy, peak energy,
  the recovery after the peak, aux hours during that recovery, and a verdict -
  *saves*, *costs*, or *no clear difference* (the difference has to be at least
  twice its standard error).

Which days count as setback days:

- **With setpoint sensors** (`cool_setpoint_entity`, `heat_setpoint_entity`) each
  weekday is labeled from what the thermostat actually did: the setpoint during
  the peak compared with the three hours before it. Once there are 5 setback and
  5 flat weekdays in a season, they are compared directly.
- **Until then**, weekdays are compared with weekends (no peak). Who's home also
  differs between them, so treat that as a hint.

The clean answer is an **A/B test**: alternate weeks with and without the setback
for 4-6 weeks per season. The page shows how many of each it has.

Every day is priced at *today's* rates, so history from before TOU billing still
answers "would this pay now?".

| Option | |
|---|---|
| `tou_peak_start`, `tou_peak_end` | `HH:MM`. Empty = section off. |
| `tou_weekdays_only` | Weekends off-peak. Default true. |
| `tou_clock` | `standard`: the times are in standard time and move an hour later in daylight saving (a peak of 3-8 PM EST = 4-9 PM EDT). `local`: they never move. |
| `tou_peak_rate`, `tou_offpeak_rate` | Per kWh. |
| `tou_peak_rate_entity`, `tou_offpeak_rate_entity` | Optional: read the rates from entities (an `input_number`) so a rate change in Home Assistant is picked up. |
| `tou_since` | `YYYY-MM-DD`: billed cost counts from here. |
| `heat_setpoint_entity`, `cool_setpoint_entity` | Numeric setpoint sensors with statistics (a template sensor of the thermostat's target temperature). |
| `currency` | Unit of the cost entities. Default `USD`. |

## Entities

With an MQTT broker (the Mosquitto add-on), the headline numbers appear as
entities on a **Maverick HVAC** device: balance points, cold knee, slopes, base
load, model fit, expected today / tomorrow, yesterday's actual / expected / index, the
30-day index, capacity limits, aux onset, aux share and hours, mild-weather aux
hours, house time constant, last analysis, and a `problem` diagnostic. With a
peak window: HVAC cost and peak share over 30 days, the shifting ceiling, and the
peak setback's value ($ a day) and verdict for cooling and heating.

Without a broker, everything is still on the add-on's page.

## Options

| Option | Default | |
|---|---|---|
| `temperature_unit` | `F` | Every temperature in and out. Statistics are converted. |
| `aux_threshold_w` | `5000` | Circuit peak above this = strips on. Set it above your maximum if there are no strips. |
| `heat_pump_running_w` | `3500` | Typical heating draw without strips; only used for the aux energy estimate. |
| `aux_mild_above` | 40 °F / 4.5 °C | Aux above this outdoor temperature counts as probably avoidable. |
| `model_days` | `365` | Fit to the most recent N complete days. |
| `history_days` | `730` | How far back to read. |
| `update_interval_minutes` | `60` | Statistics are hourly; faster rarely changes anything. |

## Limits

- A day counts only if it has nearly every hour of power and outdoor temperature.
  Gaps are listed on the page.
- One power circuit for the whole system is assumed. A mini split on its own
  circuit is a separate system - leave it out, or run it as its own analysis
  later.
- Aux energy is an estimate (circuit minus a typical heat pump draw).
- Run time without a duty sensor is estimated from power, so blower-only
  ventilation reads as a little run time.
