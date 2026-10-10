"""Serve the network map and the Tests & Evidence dashboard, and run the scenarios on request.

    python3 serve.py              # http://localhost:8000
    python3 serve.py --port 8080

Pages:   /              network map (network/index.html)
         /evidence.html Tests & Evidence dashboard
API:     GET  /api/tests        list of scenarios
         POST /api/run          run all scenarios, return results
         POST /api/run/<id>     run one scenario
         GET  /api/results      last results (runs everything if none yet)

Every result comes from running the scenarios on mpex and the network model at
request time. Nothing is cached on disk or written by hand.
"""

import argparse
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evidence.cases import CASES
from evidence.runner import BY_ID, dumps, run_all, run_case, summarize

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "network"
_lock = threading.Lock()
_last = {"results": None}


class Handler(SimpleHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tests":
            return self._json([{"id": c, "title": t} for c, t, _ in CASES])
        if self.path == "/api/results":
            with _lock:
                if _last["results"] is None:
                    _last["results"] = run_all()
                return self._json(_last["results"])
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/run":
            with _lock:
                _last["results"] = run_all()
                return self._json(_last["results"])
        if self.path.startswith("/api/run/"):
            cid = self.path.rsplit("/", 1)[-1]
            if cid not in BY_ID:
                return self._json({"error": f"unknown test {cid}"}, 404)
            with _lock:
                result = run_case(cid)
                if _last["results"]:
                    tests = [result if t["id"] == cid else t for t in _last["results"]["tests"]]
                    _last["results"] = {**_last["results"], "tests": tests, "summary": summarize(tests)}
                return self._json({"test": result,
                                   "summary": _last["results"]["summary"] if _last["results"] else None})
        self.send_error(404)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Serve the map and the evidence dashboard.")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("", args.port), partial(Handler, directory=str(STATIC)))
    print(f"Network map:          http://localhost:{args.port}/")
    print(f"Tests & Evidence:     http://localhost:{args.port}/evidence.html")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
