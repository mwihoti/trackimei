"""
server.py
---------
Tiny ingest endpoint for live snapshots from a device.

The phone POSTs the cell towers and WiFi access points it can see; the server
resolves them, records the observation, and returns the fix with a map URL.
"""

import json
import secrets
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from . import geo, history
from .locate import LocateError, locate

MAX_BODY = 128 * 1024

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VaultTrace live fix</title>
<style>body{font:16px system-ui;margin:24px;max-width:34rem}
button{padding:.8rem 1.2rem;font-size:1rem}#s{margin-top:1rem;white-space:pre-wrap}</style>
<h1>VaultTrace live fix</h1>
<p>Keep this tab open. Location is sent to your own machine.</p>
<button id="go">Start sharing location</button>
<div id="s"></div>
<script>
const p = new URLSearchParams(location.search);
const imei = p.get("imei") || "";
const token = p.get("token") || "";
const out = document.getElementById("s");
document.getElementById("go").onclick = () => {
  if (!navigator.geolocation) { out.textContent = "Geolocation unavailable"; return; }
  out.textContent = "Requesting permission...";
  navigator.geolocation.watchPosition(
    async (pos) => {
      const c = pos.coords;
      try {
        const r = await fetch("/fix?imei=" + encodeURIComponent(imei), {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-Token": token},
          body: JSON.stringify({lat: c.latitude, lon: c.longitude, accuracy: c.accuracy}),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || r.status);
        out.textContent = "Recorded " + (d.location.place || "")
          + "\\n" + d.location.lat.toFixed(5) + ", " + d.location.lon.toFixed(5)
          + "\\naccuracy about " + Math.round(d.location.accuracy_m) + " m"
          + "\\n" + new Date().toLocaleTimeString();
      } catch (e) { out.textContent = "Send failed: " + e.message; }
    },
    (err) => { out.textContent = "Permission/position error: " + err.message; },
    {enableHighAccuracy: true, maximumAge: 20000, timeout: 20000}
  );
};
</script>
"""


def _make_handler(
    token: str,
    provider: str,
    mapbox_token: Optional[str],
    google_key: Optional[str],
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vaulttrace-imei"

        def _send(self, code: int, payload: Dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, code: int, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, query: Dict) -> bool:
            supplied = self.headers.get("X-Token") or (query.get("token") or [None])[0]
            return bool(supplied) and secrets.compare_digest(supplied, token)

        def _target(self, query: Dict) -> Optional[str]:
            return self.headers.get("X-IMEI") or (query.get("imei") or [None])[0]

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                self._send_html(200, PAGE)
                return
            if parsed.path != "/status":
                self._send(404, {"error": "not found"})
                return
            if not self._authorized(query):
                self._send(401, {"error": "bad token"})
                return
            target = self._target(query)
            if not target:
                self._send(400, {"error": "missing imei"})
                return
            self._send(200, {"imei": target, "status": history.status(target)})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path not in ("/snapshot", "/fix"):
                self._send(404, {"error": "not found"})
                return
            if not self._authorized(query):
                self._send(401, {"error": "bad token"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    raise ValueError("empty or oversized body")
                payload = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})
                return

            target = self._target(query)
            if parsed.path == "/fix":
                try:
                    lat = float(payload["lat"])
                    lon = float(payload["lon"])
                except (KeyError, TypeError, ValueError):
                    self._send(400, {"error": "lat and lon required"})
                    return
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    self._send(400, {"error": "coordinates out of range"})
                    return
                accuracy = payload.get("accuracy")
                self._finish(target, lat, lon, accuracy, "device-gps", None)
                return

            cells = payload.get("cellTowers") or []
            wifi = payload.get("wifiAccessPoints") or []

            try:
                fix = locate(cells, wifi, provider=provider, google_key=google_key)
            except LocateError as exc:
                self._send(422, {"error": str(exc)})
                return

            self._finish(
                target,
                fix["lat"],
                fix["lon"],
                fix.get("accuracy_m"),
                fix.get("provider"),
                fix.get("fallback"),
            )

        def _finish(self, target, lat, lon, accuracy, provider_name, fallback) -> None:
            location = geo.location_block(
                lat,
                lon,
                token=mapbox_token,
                with_map_url=True,
                accuracy_m=accuracy,
                provider=provider_name,
                fallback=fallback,
            )
            if target:
                history.record(
                    target,
                    lat=lat,
                    lon=lon,
                    source=provider_name,
                    place=location.get("place"),
                )

            self._send(
                200,
                {
                    "ok": True,
                    "imei": target,
                    "location": location,
                    "status": history.status(target) if target else None,
                },
            )

        def log_message(self, fmt: str, *args) -> None:
            print(f"[ingest] {fmt % args}")

    return Handler


def run(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: Optional[str] = None,
    provider: str = "beacondb",
    mapbox_token: Optional[str] = None,
    google_key: Optional[str] = None,
    certfile: Optional[str] = None,
    keyfile: Optional[str] = None,
) -> None:
    token = token or secrets.token_urlsafe(18)
    handler = _make_handler(token, provider, mapbox_token, google_key)
    httpd = ThreadingHTTPServer((host, port), handler)
    scheme = "https" if certfile else "http"
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    print(f"Listening on {scheme}://{host}:{port}")
    print(f"Ingest token: {token}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
