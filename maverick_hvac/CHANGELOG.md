# Changelog

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
