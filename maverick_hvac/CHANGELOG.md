# Changelog

## 0.3.0

- **What the peak setback does to each room.** For the thermostat and every
  configured room, per season: how far it moves from the hour before the peak to
  the peak's end on setback days against comparison days in the same weather (the
  setback's extra change, with its standard error), the temperature at the end of
  the peak, the warmest (cooling) or coldest (heating) day, and how long until it
  is back where it started. A chart of each room through the afternoon and
  evening on setback days.
- New entity **Peak setback, biggest room change** (the current season's room the
  setback moves most) with every room's numbers in its attributes.

## 0.2.0

- **Time of use.** HVAC energy and cost by peak / off-peak per month and for 30
  days; the most that shifting every peak kWh could save; and, per season,
  whether a peak setback pays - setback days against flat days in the same
  weather (cost, energy, peak energy, recovery, aux in the recovery), with a
  verdict. Days are labeled from new optional setpoint sensors; until there are
  enough, weekends stand in. Supports peaks defined in standard time that move
  with daylight saving, rates read from entities, and a billing start date.
- Seven new entities when a peak window is set (cost, peak share, ceiling,
  setback value and verdict for cooling and heating).

## 0.1.1

- Two new entities: **Cold knee** (the temperature below which heating gets
  steeper) and **Extra energy per degree-day below the knee**. With the
  balance points, slopes and base load they describe the whole model, so a
  dashboard can draw the energy curve itself. Both read unknown when the data
  shows no knee.

## 0.1.0

First release.

- Reads Home Assistant long-term statistics (hourly) over the websocket API.
- Change-point model with searched heating / cooling balance points and a cold
  knee; efficiency index by day and month; expected energy today and tomorrow
  from the hourly forecast.
- Run time vs outdoor temperature and the heat pump's capacity limit; aux heat
  hours, energy, share, onset and mild-weather aux; rooms vs the thermostat by
  hour; house time constant.
- Results page over ingress (no external requests); headline entities over MQTT
  discovery.
