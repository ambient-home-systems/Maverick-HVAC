# Maverick HVAC

A Home Assistant add-on that analyzes how well your heat pump or HVAC system is
doing - from the history Home Assistant already keeps.

- **What the weather should have cost.** A degree-day model with balance points
  found from your data, a cold knee for heat pumps in deep cold, and an
  efficiency index that shows drift month by month.
- **Capacity.** Run time against outdoor temperature, and where the heat pump
  stops keeping up on its own.
- **Aux heat.** How often the electric strips run, what they cost, and how much
  of it happened in mild weather (usually avoidable).
- **Rooms.** Which rooms run warm or cold against the thermostat, and at what
  time of day.
- **The house.** How fast it drifts with the system off.

It reads Home Assistant's **long-term statistics**, so it has answers the day
it's installed if your sensors have been recording.

## Add the repository

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fambient-home-systems%2FMaverick-HVAC)

Or by hand: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, and add

```
https://github.com/ambient-home-systems/Maverick-HVAC
```

Then install **Maverick HVAC**, set the power, outdoor and indoor sensors in its
configuration, and start it. See the add-on's
[documentation](maverick_hvac/DOCS.md) for every option.

## What it needs

- A power sensor for the whole HVAC system (outdoor unit and air handler).
- An outdoor and an indoor temperature sensor.
- All with long-term statistics (a `state_class`).
- Optional: an MQTT broker for entities, a weather forecast, room sensors, a
  compressor duty sensor.

## Development

`python3 tests/run.py` runs every check against a synthetic house with known
answers. Standard library only.
