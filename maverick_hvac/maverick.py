"""Maverick HVAC - the service.

Reads Home Assistant's long-term statistics (hourly mean / min / max, kept
forever) over the websocket API, runs analysis.analyze(), publishes the headline
numbers as entities over MQTT discovery and serves the full results to the
ingress page (ui.html).

One analysis at a time, on a timer and on demand from the page. A failed run
keeps the previous results and reports the error in ``problem``; it never takes
the entities down.
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import websocket  # websocket-client

import analysis

LOG = logging.getLogger("maverick_hvac")
INGRESS_PORT = 8099  # keep in step with ingress_port in config.yaml
HERE = os.path.dirname(os.path.abspath(__file__))
OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")
WS_URL = os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket")
TOPIC = "maverick_hvac"

# What reaches Home Assistant. key = the field in results["headline"] (or a
# computed one below); everything shares one retained JSON state topic.
ENTITIES = [
    # key, name, unit ("T" = the temperature unit), device_class, state_class, icon
    ("heat_balance", "Heating balance point", "T", "temperature", "measurement", "mdi:thermometer-chevron-down"),
    ("cool_balance", "Cooling balance point", "T", "temperature", "measurement", "mdi:thermometer-chevron-up"),
    ("kwh_per_hdd", "Energy per heating degree-day", "kWh", None, "measurement", "mdi:snowflake-thermometer"),
    ("kwh_per_cdd", "Energy per cooling degree-day", "kWh", None, "measurement", "mdi:sun-thermometer"),
    ("base_kwh", "Base load", "kWh", None, "measurement", "mdi:power-plug-outline"),
    ("model_fit", "Model fit", "%", None, "measurement", "mdi:chart-bell-curve-cumulative"),
    ("expected_today", "Expected today", "kWh", None, "measurement", "mdi:calendar-today"),
    ("expected_tomorrow", "Expected tomorrow", "kWh", None, "measurement", "mdi:calendar-arrow-right"),
    ("yesterday_actual", "Yesterday actual", "kWh", None, "measurement", "mdi:calendar-check"),
    ("yesterday_expected", "Yesterday expected", "kWh", None, "measurement", "mdi:calendar-question"),
    ("yesterday_index", "Yesterday efficiency index", "%", None, "measurement", "mdi:speedometer"),
    ("recent_index", "30-day efficiency index", "%", None, "measurement", "mdi:speedometer-medium"),
    ("heat_capacity_temp", "Heat pump capacity limit", "T", "temperature", "measurement", "mdi:heat-pump-outline"),
    ("cool_capacity_temp", "Cooling capacity limit", "T", "temperature", "measurement", "mdi:snowflake-alert"),
    ("aux_onset_temp", "Aux heat onset", "T", "temperature", "measurement", "mdi:radiator"),
    ("aux_share_recent", "Aux share of heating, 30 days", "%", None, "measurement", "mdi:radiator"),
    ("aux_share_year", "Aux share of heating, 12 months", "%", None, "measurement", "mdi:radiator"),
    ("aux_hours_recent", "Aux hours, 30 days", "h", "duration", "measurement", "mdi:radiator"),
    ("aux_mild_hours_recent", "Mild-weather aux hours, 30 days", "h", "duration", "measurement", "mdi:radiator-off"),
    ("aux_mild_hours_year", "Mild-weather aux hours, 12 months", "h", "duration", "measurement", "mdi:radiator-off"),
    ("time_constant", "House time constant", "h", "duration", "measurement", "mdi:home-thermometer-outline"),
    ("last_analysis", "Last analysis", None, "timestamp", None, "mdi:clock-check-outline"),
    ("problem", "Problem", None, None, None, "mdi:alert-circle-outline"),
]


# ----------------------------------------------------------------------------
# Home Assistant
# ----------------------------------------------------------------------------

class HA:
    """Minimal synchronous websocket client - one request at a time."""

    def __init__(self, url, token):
        self.url, self.token, self.ws, self.n = url, token, None, 0

    def __enter__(self):
        self.ws = websocket.create_connection(self.url, timeout=120)
        hello = json.loads(self.ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {hello}")
        self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        ok = json.loads(self.ws.recv())
        if ok.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant refused the token")
        return self

    def __exit__(self, *a):
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, msg):
        self.n += 1
        msg = dict(msg, id=self.n)
        self.ws.send(json.dumps(msg))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.n and r.get("type") == "result":
                if not r.get("success"):
                    raise RuntimeError(f"{msg['type']}: {r.get('error')}")
                return r.get("result")

    def statistics(self, ids, start, end, units):
        res = self.call({"type": "recorder/statistics_during_period", "start_time": start.isoformat(),
                         "end_time": end.isoformat(), "statistic_ids": ids, "period": "hour",
                         "types": ["mean", "min", "max"], "units": units})
        out = {}
        for sid, rows in (res or {}).items():
            d = {}
            for r in rows:
                st = r["start"]
                ts = int(st / 1000) if isinstance(st, (int, float)) else int(datetime.fromisoformat(st).timestamp())
                d[ts] = {"mean": r.get("mean"), "min": r.get("min"), "max": r.get("max")}
            out[sid] = d
        return out

    def forecast(self, entity):
        r = self.call({"type": "call_service", "domain": "weather", "service": "get_forecasts",
                       "target": {"entity_id": entity}, "service_data": {"type": "hourly"}, "return_response": True})
        return ((r or {}).get("response") or {}).get(entity, {}).get("forecast", [])


def fetch(opts):
    """Everything analyze() needs, as role -> {hour_ts: {...}}, plus Home
    Assistant's time zone (days, hours of day and the forecast split use it)."""
    unit = opts["temperature_unit"]
    roles = {"power": opts["power_entity"], "outdoor": opts["outdoor_temperature_entity"],
             "indoor": opts["indoor_temperature_entity"]}
    if opts.get("compressor_duty_entity"):
        roles["duty"] = opts["compressor_duty_entity"]
    names = {}
    for r in opts.get("rooms") or []:
        roles["room:" + r["entity"]] = r["entity"]
        names[r["entity"]] = r.get("name") or r["entity"]
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=int(opts["history_days"]))
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN")
    with HA(WS_URL, token) as ha:
        cfg = ha.call({"type": "get_config"})
        tz = ZoneInfo(cfg.get("time_zone") or "UTC")
        stats = ha.statistics(sorted(set(roles.values())), start, end,
                              {"temperature": "°" + unit, "power": "W"})
        fc = []
        if opts.get("weather_entity"):
            try:
                fc = ha.forecast(opts["weather_entity"])
            except Exception as e:  # a missing forecast only costs the "expected" numbers
                LOG.warning("forecast unavailable: %s", e)
    series = {role: stats.get(sid, {}) for role, sid in roles.items()}
    missing = [sid for role, sid in roles.items() if not series[role]]
    now = datetime.now(tz)
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    f_today, f_tom = [], []
    for f in fc:
        t = f.get("temperature")
        if not isinstance(t, (int, float)):
            continue
        dt = datetime.fromisoformat(f["datetime"]).astimezone(tz)
        if dt.date() == today and dt > now:
            f_today.append(float(t))
        elif dt.date() == tomorrow:
            f_tom.append(float(t))
    return series, names, {"today": f_today, "tomorrow": f_tom}, missing, tz


# ----------------------------------------------------------------------------
# MQTT
# ----------------------------------------------------------------------------

class Publisher:
    def __init__(self, unit):
        self.unit = unit
        self.client = None
        host = os.environ.get("MQTT_HOST")
        if not host:
            LOG.warning("no MQTT broker - results are only on the add-on page")
            return
        import paho.mqtt.client as mqtt
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="maverick_hvac")
        except AttributeError:  # paho 1.x
            c = mqtt.Client(client_id="maverick_hvac")
        if os.environ.get("MQTT_USER"):
            c.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASSWORD"))
        c.will_set(f"{TOPIC}/status", "offline", retain=True)
        c.on_connect = self._on_connect
        c.connect_async(host, int(os.environ.get("MQTT_PORT", "1883")))
        c.loop_start()
        self.client = c
        self.last = None

    def _on_connect(self, c, *a):
        dev = {"identifiers": ["maverick_hvac"], "name": "Maverick HVAC", "manufacturer": "Ambient Home Systems",
               "model": "HVAC efficiency analysis"}
        for key, name, unit, dc, sc, icon in ENTITIES:
            cfg = {"name": name, "unique_id": f"maverick_hvac_{key}", "object_id": f"maverick_hvac_{key}",
                   "state_topic": f"{TOPIC}/state", "value_template": "{{ value_json.%s }}" % key,
                   "availability_topic": f"{TOPIC}/status", "device": dev, "icon": icon}
            if unit:
                cfg["unit_of_measurement"] = "°" + self.unit if unit == "T" else unit
            if dc:
                cfg["device_class"] = dc
            if sc:
                cfg["state_class"] = sc
            if key == "problem":
                cfg["entity_category"] = "diagnostic"
            c.publish(f"homeassistant/sensor/maverick_hvac/{key}/config", json.dumps(cfg), retain=True)
        c.publish(f"{TOPIC}/status", "online", retain=True)
        if self.last:
            c.publish(f"{TOPIC}/state", self.last, retain=True)

    def publish(self, state):
        self.last = json.dumps(state)
        if self.client:
            self.client.publish(f"{TOPIC}/state", self.last, retain=True)


def headline_state(res, problem):
    m = (res or {}).get("model") or {}
    h = dict((res or {}).get("headline") or {})
    h.update({
        "heat_balance": m.get("heat_balance"), "cool_balance": m.get("cool_balance"),
        "kwh_per_hdd": m.get("kwh_per_hdd"), "kwh_per_cdd": m.get("kwh_per_cdd"), "base_kwh": m.get("base_kwh"),
        "model_fit": round(100 * m["r2"], 1) if m.get("r2") is not None else None,
        "time_constant": round(res["coast"]["tau_h"], 1) if res and (res.get("coast") or {}).get("tau_h") else None,
        "last_analysis": (res or {}).get("generated"),
        "problem": problem or "none",
    })
    return h


# ----------------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------------

class Service:
    def __init__(self, opts):
        self.opts = opts
        self.results = None
        self.problem = None
        self.running = False
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.publisher = Publisher(opts["temperature_unit"])

    def run_once(self):
        with self.lock:
            if self.running:
                return False
            self.running = True
        t0 = time.time()
        try:
            series, names, fc, missing, tz = fetch(self.opts)
            s = analysis.Settings(tz, unit=self.opts["temperature_unit"],
                                  aux_threshold_w=self.opts.get("aux_threshold_w"),
                                  heat_pump_running_w=self.opts.get("heat_pump_running_w"),
                                  aux_mild_above=self.opts.get("aux_mild_above"),
                                  model_days=self.opts.get("model_days"))
            res = analysis.analyze(series, s, time.time(), names=names, forecast=fc)
            res["missing"] = missing
            res["elapsed_s"] = round(time.time() - t0, 1)
            res["options"] = dict(self.opts)
            problems = []
            if missing:
                problems.append("no statistics for " + ", ".join(missing))
            if not res.get("model"):
                problems.append("not enough complete days to fit the model (needs 30)")
            self.results = res
            self.problem = "; ".join(problems) or None
            LOG.info("analysis done in %.1f s: %d complete days, model %s", res["elapsed_s"],
                     res["coverage"]["complete_days"], "ok" if res.get("model") else "none")
        except Exception as e:
            LOG.exception("analysis failed")
            self.problem = f"analysis failed: {e}"
        finally:
            self.publisher.publish(headline_state(self.results, self.problem))
            with self.lock:
                self.running = False
        return True

    def loop(self):
        interval = max(5, int(self.opts.get("update_interval_minutes") or 60)) * 60
        while True:
            self.run_once()
            self.wake.wait(interval)
            self.wake.clear()


def make_handler(svc):
    page = os.path.join(HERE, "ui.html")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            LOG.debug("http: " + fmt, *a)

        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            if path in ("", "index.html"):
                with open(page, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "results":
                return self._send(200, json.dumps({"results": svc.results, "problem": svc.problem,
                                                   "running": svc.running}), "application/json")
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            if self.path.rstrip("/").endswith("refresh"):
                svc.wake.set()
                return self._send(202, json.dumps({"started": True}), "application/json")
            self._send(404, "not found", "text/plain")

    return H


def main():
    with open(OPTIONS_PATH) as f:
        opts = json.load(f)
    logging.basicConfig(level=getattr(logging, str(opts.get("log_level", "info")).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    svc = Service(opts)
    threading.Thread(target=svc.loop, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", INGRESS_PORT), make_handler(svc)) as srv:
        LOG.info("Maverick HVAC listening on %d", INGRESS_PORT)
        srv.serve_forever()


if __name__ == "__main__":
    main()
