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

## Entities

With an MQTT broker (the Mosquitto add-on), the headline numbers appear as
entities on a **Maverick HVAC** device: balance points, cold knee, slopes, base
load, model fit, expected today / tomorrow, yesterday's actual / expected / index, the
30-day index, capacity limits, aux onset, aux share and hours, mild-weather aux
hours, house time constant, last analysis, and a `problem` diagnostic.

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
