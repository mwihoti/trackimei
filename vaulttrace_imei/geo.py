"""
geo.py
------
Optional Mapbox helpers for attaching a human-readable place and a rendered
map to an IMEI decode result.

Mapbox does not locate a device from an IMEI. It reverse-geocodes coordinates
that you already have (for example from a cell/WiFi snapshot or GPS fix) and
renders a static map image for them.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

GEOCODE_URL = "https://api.mapbox.com/geocoding/v5/mapbox.places/{lon},{lat}.json"
STATIC_URL = (
    "https://api.mapbox.com/styles/v1/mapbox/streets-v12/static/"
    "pin-s+ff3b30({lon},{lat})/{lon},{lat},{zoom}/{width}x{height}@2x"
)
HTTP_TIMEOUT = 10
TOKEN_ENV = "MAPBOX_TOKEN"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILES = (
    os.path.join(PROJECT_DIR, ".env"),
    os.path.join(PROJECT_DIR, ".env.example"),
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.getcwd(), ".env.example"),
    os.path.join(os.path.expanduser("~"), ".config", "vaulttrace-imei", "token"),
)


class MapboxError(RuntimeError):
    pass


def _token_from_file(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    return line.strip("'\"")
                key, _, value = line.partition("=")
                if key.strip() == TOKEN_ENV:
                    return value.strip().strip("'\"")
    except OSError:
        pass
    return None


def resolve_token(token: Optional[str] = None) -> str:
    token = token or os.environ.get(TOKEN_ENV)
    if token:
        return token

    for path in TOKEN_FILES:
        token = _token_from_file(path)
        if token:
            return token

    raise MapboxError(
        "Mapbox token required. Put MAPBOX_TOKEN=pk... in .env, set the "
        "variable, or pass --mapbox-token."
    )


def token_available(token: Optional[str] = None) -> bool:
    if token or os.environ.get(TOKEN_ENV):
        return True
    return any(_token_from_file(path) for path in TOKEN_FILES)


def _get_json(url: str) -> Dict:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except OSError:
            pass
        if exc.code == 401:
            raise MapboxError("Mapbox rejected the token (401).") from exc
        raise MapboxError(f"Mapbox request failed ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MapboxError(f"Mapbox request failed: {exc}") from exc


def reverse_geocode(lat: float, lon: float, token: Optional[str] = None) -> Dict:
    """Return a place description for a coordinate, or an empty result."""
    token = resolve_token(token)
    query = urllib.parse.urlencode(
        {
            "access_token": token,
            "types": "address,place,locality,region,country",
            "limit": 1,
        }
    )
    data = _get_json(GEOCODE_URL.format(lon=lon, lat=lat) + "?" + query)
    features = data.get("features") or []
    if not features:
        return {}

    feature = features[0]
    return {
        "place": feature.get("place_name", ""),
        "place_type": (feature.get("place_type") or [None])[0],
    }


def static_map_url(
    lat: float,
    lon: float,
    token: Optional[str] = None,
    zoom: int = 14,
    width: int = 600,
    height: int = 400,
) -> str:
    token = resolve_token(token)
    url = STATIC_URL.format(
        lat=lat, lon=lon, zoom=zoom, width=width, height=height
    )
    return url + "?" + urllib.parse.urlencode({"access_token": token})


def download_static_map(
    lat: float,
    lon: float,
    path: str,
    token: Optional[str] = None,
    zoom: int = 14,
) -> str:
    url = static_map_url(lat, lon, token=token, zoom=zoom)
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise MapboxError(f"Mapbox Static Images failed ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MapboxError(f"Mapbox Static Images failed: {exc}") from exc

    with open(path, "wb") as handle:
        handle.write(payload)
    return url


def location_block(
    lat: float,
    lon: float,
    token: Optional[str] = None,
    with_map_url: bool = False,
    accuracy_m: Optional[float] = None,
    provider: Optional[str] = None,
    fallback: Optional[str] = None,
) -> Dict:
    """Build the location block attached to a decode result."""
    block: Dict = {"lat": lat, "lon": lon, "source": provider or "user-supplied"}
    if accuracy_m is not None:
        block["accuracy_m"] = accuracy_m
    if fallback:
        block["fallback"] = fallback

    if not token_available(token):
        block["place"] = None
        block["notes"] = ["No Mapbox token; reverse geocoding skipped."]
        return block

    try:
        place = reverse_geocode(lat, lon, token=token)
        block["place"] = place.get("place")
        block["place_type"] = place.get("place_type")
        if with_map_url:
            block["map_url"] = static_map_url(lat, lon, token=token)
    except MapboxError as exc:
        block["place"] = None
        block["error"] = str(exc)

    return block
