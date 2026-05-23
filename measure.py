#!/usr/bin/env python3
"""CLI + HTTP server for the roof measurement engine.

CLI:
    python measure.py "123 Main St, Toronto, ON"
    python measure.py --synthetic "123 Main St"
    python measure.py --strategy solar_first "123 Main St"

HTTP server:
    python measure.py --serve 8088

Endpoints:
    GET  /health                no auth, returns engine status
    POST /measure               X-API-Key required, runs measurement
    GET  /admin/strategy        X-Admin-Key required, returns current strategy
    POST /admin/strategy        X-Admin-Key required, body {"strategy": "auto"|...}

Env vars:
    ROOFMEASURE_API_KEY              - X-API-Key gate for /measure
    ROOFMEASURE_ADMIN_KEY            - X-Admin-Key gate for /admin/*
    ROOFMEASURE_STRATEGY             - default strategy (overridden by runtime config)
    ROOFMEASURE_CONFIG_FILE          - persisted runtime config path
    GOOGLE_SOLAR_API_KEY             - required for Solar API engine
    GOOGLE_GEOCODING_API_KEY         - optional, improves geocode accuracy
    OPENTOPO_API_KEY                 - optional, simplest LiDAR path
"""
from __future__ import annotations
import argparse
import hmac
import json
import logging
import os
import sys

from roofmeasure.measurement import measure_roof
from roofmeasure.runtime_config import (
    VALID_STRATEGIES, get_runtime_config, get_strategy, set_runtime_config,
)
from roofmeasure.usage import summary as usage_summary


def cli() -> int:
    parser = argparse.ArgumentParser(description="Roof measurement engine")
    parser.add_argument("address", nargs="?", help="Property address")
    parser.add_argument("--synthetic", action="store_true",
                        help="Skip real LiDAR fetch, synthesize a hip roof for the footprint")
    parser.add_argument("--strategy",
                        choices=list(VALID_STRATEGIES),
                        help="Override engine strategy")
    parser.add_argument("--out", help="Write JSON to file instead of stdout")
    parser.add_argument("--serve", type=int, metavar="PORT", help="Run HTTP server on this port")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.serve:
        return serve_http(args.serve)

    if not args.address:
        parser.error("address is required unless --serve is used")

    result = measure_roof(args.address, use_synthetic_lidar=args.synthetic,
                          strategy=args.strategy)
    payload = result.to_json()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        print("wrote %s" % args.out)
    else:
        print(payload)
    return 0


def _key_ok(expected_env: str, provided: str) -> bool:
    expected = os.environ.get(expected_env, "").strip()
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def serve_http(port: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    api_key_configured = bool(os.environ.get("ROOFMEASURE_API_KEY", "").strip())
    admin_key_configured = bool(os.environ.get("ROOFMEASURE_ADMIN_KEY", "").strip())

    class Handler(BaseHTTPRequestHandler):
        server_version = "RoofMeasureEngine/0.3"

        def _send_json(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            allow = os.environ.get("ROOFMEASURE_ALLOW_ORIGIN")
            if allow:
                self.send_header("Access-Control-Allow-Origin", allow)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, X-Admin-Key")
                self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            logging.info("HTTP %s - %s", self.address_string(), fmt % args)

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 100_000:
                self._send_json(400, {"error": "Content-Length missing or out of range"})
                return None
            try:
                return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return None

        def do_OPTIONS(self):
            self._send_json(200, {"ok": True})

        def do_GET(self):
            if self.path == "/health":
                self._send_json(200, {
                    "status": "ok",
                    "auth_required": api_key_configured,
                    "admin_enabled": admin_key_configured,
                    "version": self.server_version,
                    "strategy": get_strategy(),
                })
                return
            if self.path == "/admin/strategy":
                if not _key_ok("ROOFMEASURE_ADMIN_KEY", self.headers.get("X-Admin-Key", "")):
                    self._send_json(401, {"error": "invalid or missing X-Admin-Key"})
                    return
                cfg = get_runtime_config()
                self._send_json(200, {
                    "strategy": get_strategy(),
                    "envDefault": os.environ.get("ROOFMEASURE_STRATEGY", "auto"),
                    "persisted": cfg,
                    "validStrategies": sorted(VALID_STRATEGIES),
                })
                return
            if self.path.startswith("/admin/usage"):
                if not _key_ok("ROOFMEASURE_ADMIN_KEY", self.headers.get("X-Admin-Key", "")):
                    self._send_json(401, {"error": "invalid or missing X-Admin-Key"})
                    return
                # Parse optional from=YYYY-MM-DD&to=YYYY-MM-DD query string
                from urllib.parse import urlparse, parse_qs
                import time as _t
                qs = parse_qs(urlparse(self.path).query)
                def _parse_iso(s):
                    try:
                        return int(_t.mktime(_t.strptime(s, "%Y-%m-%d")))
                    except (ValueError, TypeError):
                        return None
                from_e = _parse_iso(qs.get("from", [None])[0]) if qs.get("from") else None
                to_e = _parse_iso(qs.get("to", [None])[0]) if qs.get("to") else None
                self._send_json(200, usage_summary(from_epoch=from_e, to_epoch=to_e))
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            # ---- /admin/strategy ----
            if self.path == "/admin/strategy":
                if not _key_ok("ROOFMEASURE_ADMIN_KEY", self.headers.get("X-Admin-Key", "")):
                    logging.warning("rejected /admin/strategy from %s: bad X-Admin-Key",
                                    self.address_string())
                    self._send_json(401, {"error": "invalid or missing X-Admin-Key"})
                    return
                body = self._read_json_body()
                if body is None:
                    return
                new_strategy = (body.get("strategy") or "").strip()
                if new_strategy not in VALID_STRATEGIES:
                    self._send_json(400, {
                        "error": "invalid strategy",
                        "validStrategies": sorted(VALID_STRATEGIES),
                    })
                    return
                who = body.get("who") or self.headers.get("X-Admin-User", "admin")
                cfg = set_runtime_config({"strategy": new_strategy}, who=str(who))
                logging.info("strategy changed to %s by %s", new_strategy, who)
                self._send_json(200, {
                    "strategy": get_strategy(),
                    "persisted": cfg,
                })
                return

            # ---- /measure ----
            if self.path == "/measure":
                if not _key_ok("ROOFMEASURE_API_KEY", self.headers.get("X-API-Key", "")):
                    logging.warning("rejected /measure from %s: bad or missing X-API-Key",
                                    self.address_string())
                    self._send_json(401, {"error": "invalid or missing X-API-Key header"})
                    return
                body = self._read_json_body()
                if body is None:
                    return
                address = (body.get("address") or "").strip()
                if not address:
                    self._send_json(400, {"error": "address required"})
                    return
                if len(address) > 500:
                    self._send_json(400, {"error": "address too long"})
                    return
                try:
                    result = measure_roof(
                        address,
                        use_synthetic_lidar=bool(body.get("synthetic")),
                        strategy=body.get("strategy"),
                    )
                    self._send_json(200, json.loads(result.to_json()))
                except Exception as exc:
                    logging.exception("measurement failed for %s", address)
                    self._send_json(500, {"error": str(exc), "address": address})
                return

            self._send_json(404, {"error": "not found"})

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    msg = "listening on http://0.0.0.0:%d" % port
    if api_key_configured:
        msg += " (X-API-Key required for /measure"
    else:
        msg += " (NO X-API-Key configured - open mode"
    msg += "; admin %s)" % ("enabled" if admin_key_configured else "disabled")
    logging.info(msg)
    if not api_key_configured:
        logging.warning("Set ROOFMEASURE_API_KEY before exposing this server publicly.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(cli())
