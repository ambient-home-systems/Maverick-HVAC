"""The service glue: entity list, headline state, the page's HTTP endpoints."""

import io
import json
import os
import sys
import types

FAILS = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def _import():
    # websocket-client is only needed to talk to Home Assistant; stub it if absent
    if "websocket" not in sys.modules:
        try:
            import websocket  # noqa: F401
        except ImportError:
            sys.modules["websocket"] = types.ModuleType("websocket")
    import maverick
    return maverick


def test_entities_have_state():
    mv = _import()
    import synth, analysis
    from datetime import timezone
    series, t0, t1 = synth.house()
    res = analysis.analyze(series, analysis.Settings(timezone.utc), t1)
    st = mv.headline_state(res, None)
    missing = [k for k, *_ in mv.ENTITIES if k not in st]
    check("every entity has a state key", not missing, missing)
    check("problem reads 'none' when healthy", st["problem"] == "none")
    st2 = mv.headline_state(None, "boom")
    check("no results yet still produces a state", st2["problem"] == "boom" and st2["heat_balance"] is None)
    keys = [k for k, *_ in mv.ENTITIES]
    check("entity keys unique", len(keys) == len(set(keys)))
    page = open(os.path.join(os.path.dirname(mv.__file__), "ui.html")).read()
    check("page uses only relative URLs", 'fetch("/' not in page and "fetch('/" not in page)


def test_http():
    mv = _import()

    class Svc:
        results = {"x": 1}
        problem = None
        running = False

        class wake:
            hit = False

            @classmethod
            def set(cls):
                cls.hit = True

    H = mv.make_handler(Svc)

    def call(method, path):
        h = H.__new__(H)
        h.path, h.wfile, h.headers = path, io.BytesIO(), {}
        h.requestline, h.request_version, h.command = f"{method} {path} HTTP/1.1", "HTTP/1.1", method
        h.close_connection = True
        sent = {}
        h.send_response = lambda code, msg=None: sent.setdefault("code", code)
        h.send_header = lambda *a: None
        h.end_headers = lambda: None
        getattr(h, "do_" + method)()
        return sent.get("code"), h.wfile.getvalue()

    code, body = call("GET", "/")
    check("page served", code == 200 and b"Maverick HVAC" in body)
    code, body = call("GET", "/results")
    check("results served", code == 200 and json.loads(body)["results"] == {"x": 1})
    code, body = call("POST", "/refresh")
    check("refresh wakes the loop", code == 202 and Svc.wake.hit)
    code, _ = call("GET", "/../../etc/passwd")
    check("unknown paths 404", code == 404)


def run():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(f"-- {name}")
            fn()
    return FAILS
