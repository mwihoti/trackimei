"""
locate.py
---------
Resolve coordinates from a cell/WiFi radio snapshot.

An IMEI does not contain a position. These providers geolocate the towers and
access points a device can currently see. beaconDB is keyless and open;
Google's Geolocation API is optional and requires an API key.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

BEACONDB_URL = "https://api.beacondb.net/v1/geolocate"
GOOGLE_URL = "https://www.googleapis.com/geolocation/v1/geolocate"
GOOGLE_KEY_ENV = "GOOGLE_MAPS_API_KEY"
HTTP_TIMEOUT = 15
USER_AGENT = "vaulttrace-imei/0.1 (+https://github.com/theosintvault/vaulttrace-imei)"


class LocateError(RuntimeError):
    pass


def _post(url: str, payload: Dict) -> Dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except OSError:
            pass
        raise LocateError(f"Geolocation failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LocateError(f"Geolocation failed: {exc}") from exc


def locate(
    cells: List[Dict],
    wifi: List[Dict],
    provider: str = "beacondb",
    google_key: Optional[str] = None,
) -> Dict:
    if not cells and not wifi:
        raise LocateError("Provide at least one --cell or --wifi entry.")

    payload: Dict = {"considerIp": True}
    if cells:
        payload["cellTowers"] = cells
    if wifi:
        payload["wifiAccessPoints"] = wifi

    if provider == "google":
        key = google_key or os.environ.get(GOOGLE_KEY_ENV)
        if not key:
            raise LocateError(
                "Google provider needs GOOGLE_MAPS_API_KEY or --google-key."
            )
        data = _post(f"{GOOGLE_URL}?key={key}", payload)
    elif provider == "beacondb":
        data = _post(BEACONDB_URL, payload)
    else:
        raise LocateError(f"Unknown provider '{provider}'.")

    location = data.get("location") or {}
    if "lat" not in location or "lng" not in location:
        raise LocateError("Provider returned no location.")

    fix = {
        "lat": float(location["lat"]),
        "lon": float(location["lng"]),
        "accuracy_m": data.get("accuracy"),
        "provider": provider,
    }
    if data.get("fallback"):
        fix["fallback"] = data["fallback"]
        fix["notes"] = [
            f"Provider fell back to {data['fallback']}; accuracy is coarse."
        ]
    return fix
