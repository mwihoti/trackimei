import json
import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, List

from .carrier_ranges import infer_carrier
from .geo import MapboxError, download_static_map, location_block
from . import history
from .locate import LocateError, locate as locate_device
from .phone import NumberError, parse_number
from .tac_db import TAC_DB
from .tac_lookup import basic_tac_profile as remote_tac_profile


def _parse_cell(spec: str, radio: str) -> Dict[str, Any]:
    try:
        mcc, mnc, lac, cid, *rest = spec.split(",")
        cell: Dict[str, Any] = {
            "radioType": radio,
            "mobileCountryCode": int(mcc),
            "mobileNetworkCode": int(mnc),
            "locationAreaCode": int(lac),
            "cellId": int(cid),
        }
    except ValueError as exc:
        raise ValueError(
            f"Invalid --cell '{spec}'. Use MCC,MNC,LAC,CID[,RSSI]."
        ) from exc
    if rest:
        cell["signalStrength"] = int(rest[0])
    return cell


def _parse_wifi(spec: str) -> Dict[str, Any]:
    mac, *rest = spec.split(",")
    if not mac:
        raise ValueError(f"Invalid --wifi '{spec}'. Use BSSID[,RSSI].")
    entry: Dict[str, Any] = {"macAddress": mac}
    if rest:
        entry["signalStrength"] = int(rest[0])
    return entry


def _load_snapshot(path: str) -> Dict[str, List[Dict]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read snapshot '{path}': {exc}") from exc
    return {
        "cells": data.get("cellTowers") or [],
        "wifi": data.get("wifiAccessPoints") or [],
    }


def luhn_checksum(imei: str) -> int:
    digits = [int(d) for d in imei]
    for i in range(len(digits) - 2, -1, -2):
        doubled = digits[i] * 2
        digits[i] = doubled - 9 if doubled > 9 else doubled
    return sum(digits) % 10


def validate_imei(imei: str) -> bool:
    return len(imei) == 15 and imei.isdigit() and luhn_checksum(imei) == 0


def basic_tac_profile(tac: str, offline: bool = False) -> Dict[str, Any]:
    if tac in TAC_DB:
        entry = TAC_DB[tac]
        return {
            "tac": tac,
            "manufacturer": entry.get("manufacturer", "Unknown"),
            "model": entry.get("model", "Unknown"),
            "region": entry.get("region", "Unknown"),
            "confidence": entry.get("confidence", 0.0),
            "notes": entry.get("notes", []),
            "device_type": entry.get("device_type", "Unknown"),
            "release_year": entry.get("release_year", "Unknown"),
        }

    if not offline:
        try:
            remote = remote_tac_profile(tac)
        except Exception:
            remote = None
        if remote and remote.get("manufacturer", "Unknown") != "Unknown":
            profile = {
                "tac": tac,
                "manufacturer": remote.get("manufacturer", "Unknown"),
                "model": remote.get("model", "Unknown"),
                "region": remote.get("region", "Unknown"),
                "confidence": remote.get("confidence", 0.0),
                "notes": remote.get("notes", []),
                "device_type": remote.get("device_type", "Unknown"),
                "release_year": remote.get("release_year", "Unknown"),
            }
            TAC_DB[tac] = profile
            return profile

    notes = ["No TAC match found in local database."]
    if tac.startswith("35"):
        notes.append("TAC starts with '35' (common smartphone allocation).")
    if tac.startswith("01"):
        notes.append("TAC starts with '01' (older allocation pattern).")

    return {
        "tac": tac,
        "manufacturer": "Unknown",
        "model": "Unknown",
        "region": "Unknown",
        "confidence": 0.0,
        "notes": notes,
        "device_type": "Unknown",
        "release_year": "Unknown",
    }


def detect_anomalies(imei: str, profile: Dict[str, Any]) -> List[Dict[str, str]]:
    anomalies: List[Dict[str, str]] = []

    if imei == imei[0] * 15:
        anomalies.append({
            "msg": "IMEI is composed of a single repeated digit.",
            "severity": "high",
        })

    if profile["tac"].startswith("00"):
        anomalies.append({
            "msg": "TAC starts with '00' (test or unallocated range).",
            "severity": "high",
        })

    if profile.get("confidence", 0.0) < 0.5:
        anomalies.append({
            "msg": "Low confidence TAC match.",
            "severity": "medium",
        })

    carrier = profile.get("likely_original_carrier")
    region = profile.get("region")
    if carrier and region and carrier != "Unknown" and region not in ("Unknown", "Global"):
        if carrier.lower() not in region.lower():
            anomalies.append({
                "msg": f"Carrier '{carrier}' does not appear to match region '{region}'.",
                "severity": "low",
            })

    return anomalies


def _build_result(
    imei: str, tac: str, profile: Dict[str, Any], valid: bool
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "imei": imei,
        "valid": valid,
        "tac": tac,
        "manufacturer": profile["manufacturer"],
        "model": profile["model"],
        "region": profile["region"],
        "confidence": profile["confidence"],
        "likely_original_carrier": infer_carrier(tac, profile["region"]),
        "notes": profile["notes"],
        "device_type": profile["device_type"],
        "release_year": profile["release_year"],
    }
    result["anomalies"] = detect_anomalies(imei, result)
    return result


def decode_imei(imei: str, offline: bool = False) -> Dict[str, Any]:
    if not validate_imei(imei):
        return {
            "imei": imei,
            "valid": False,
            "error": "Invalid IMEI (Luhn or length failed).",
        }

    tac = imei[:8]
    profile = basic_tac_profile(tac, offline=offline)
    return _build_result(imei, tac, profile, True)


def decode_tac(tac: str, offline: bool = False) -> Dict[str, Any]:
    result = _build_result(tac, tac, basic_tac_profile(tac, offline=offline), True)
    result["imei"] = None
    result["validation"] = "tac-only"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="VaultTrace IMEI/TAC Decoder (local TAC DB + public fallback)"
    )
    parser.add_argument(
        "identifiers",
        nargs="*",
        help="IMEI(s) to decode (15-digit) or TAC(s) (8-digit, best-effort).",
    )
    parser.add_argument(
        "--msisdn",
        help="Phone number / line to profile (E.164, or national with --region).",
    )
    parser.add_argument(
        "--region",
        default="KE",
        help="Default region for national numbers (default: KE).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip online TAC lookup and use local data only.",
    )
    parser.add_argument(
        "--lat",
        type=float,
        help="Latitude of the device's last known position.",
    )
    parser.add_argument(
        "--lon",
        type=float,
        help="Longitude of the device's last known position.",
    )
    parser.add_argument(
        "--locate",
        action="store_true",
        help="Geolocate from --cell/--wifi or --snapshot instead of --lat/--lon.",
    )
    parser.add_argument(
        "--cell",
        action="append",
        metavar="MCC,MNC,LAC,CID[,RSSI]",
        help="Cell tower observed by the device (repeatable).",
    )
    parser.add_argument(
        "--wifi",
        action="append",
        metavar="BSSID[,RSSI]",
        help="WiFi access point observed by the device (repeatable).",
    )
    parser.add_argument(
        "--radio",
        default="gsm",
        choices=("gsm", "wcdma", "lte", "nr"),
        help="Radio type for --cell entries (default: gsm).",
    )
    parser.add_argument(
        "--snapshot",
        metavar="PATH",
        help="JSON file with cellTowers/wifiAccessPoints arrays.",
    )
    parser.add_argument(
        "--provider",
        default="beacondb",
        choices=("beacondb", "google"),
        help="Geolocation provider (default: beacondb, keyless).",
    )
    parser.add_argument(
        "--google-key",
        help="Google Geolocation API key (defaults to GOOGLE_MAPS_API_KEY).",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Append this observation (IMEI + location) to local history.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show last-seen inference from local history.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show recent recorded observations for the identifier.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=10,
        help="Number of history entries to show (default: 10).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run an ingest server for live snapshots instead of decoding.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Serve bind address.")
    parser.add_argument("--port", type=int, default=8765, help="Serve port.")
    parser.add_argument(
        "--server-token",
        help="Ingest token (defaults to VAULTTRACE_SERVER_TOKEN or generated).",
    )
    parser.add_argument("--cert", help="TLS certificate file for --serve.")
    parser.add_argument("--key", help="TLS private key file for --serve.")
    parser.add_argument(
        "--mapbox-token",
        help="Mapbox access token (defaults to MAPBOX_TOKEN).",
    )
    parser.add_argument(
        "--map",
        dest="map_path",
        metavar="PATH",
        help="Write a Mapbox static map image for --lat/--lon to PATH.",
    )
    parser.add_argument(
        "--map-zoom",
        type=int,
        default=14,
        help="Zoom level for --map output (default: 14).",
    )
    args = parser.parse_args()

    if not args.serve and not args.identifiers and not args.msisdn:
        parser.error("Provide at least one identifier or --msisdn.")

    if args.serve:
        from .server import run as serve

        serve(
            host=args.host,
            port=args.port,
            token=args.server_token or os.environ.get("VAULTTRACE_SERVER_TOKEN"),
            provider=args.provider,
            mapbox_token=args.mapbox_token,
            google_key=args.google_key,
            certfile=args.cert,
            keyfile=args.key,
        )
        return

    line_block = None
    if args.msisdn:
        try:
            line_block = parse_number(args.msisdn, region=args.region)
        except NumberError as exc:
            parser.error(str(exc))

    if args.locate and (args.lat is not None or args.lon is not None):
        parser.error("--locate cannot be combined with --lat/--lon.")
    if (args.lat is None) != (args.lon is None) and not args.locate:
        parser.error("--lat and --lon must be supplied together.")
    if args.lat is not None and not -90 <= args.lat <= 90:
        parser.error("--lat must be between -90 and 90.")
    if args.lon is not None and not -180 <= args.lon <= 180:
        parser.error("--lon must be between -180 and 180.")
    if args.map_path and args.lat is None and not args.locate:
        parser.error("--map requires --lat/--lon or --locate.")
    if args.record and args.lat is None and not args.locate:
        parser.error("--record requires --lat/--lon or --locate.")

    cells: List[Dict] = []
    wifi: List[Dict] = []
    if args.snapshot:
        try:
            snapshot = _load_snapshot(args.snapshot)
        except ValueError as exc:
            parser.error(str(exc))
        cells.extend(snapshot["cells"])
        wifi.extend(snapshot["wifi"])
    try:
        cells.extend(_parse_cell(spec, args.radio) for spec in args.cell or [])
        wifi.extend(_parse_wifi(spec) for spec in args.wifi or [])
    except ValueError as exc:
        parser.error(str(exc))

    if args.locate and not cells and not wifi:
        parser.error("--locate requires --cell, --wifi or --snapshot data.")

    lat: Any = args.lat
    lon: Any = args.lon
    fix: Dict[str, Any] = {}
    if args.locate:
        try:
            fix = locate_device(
                cells,
                wifi,
                provider=args.provider,
                google_key=args.google_key,
            )
        except LocateError as exc:
            print(f"Locate error: {exc}", file=sys.stderr)
            sys.exit(1)
        lat, lon = fix["lat"], fix["lon"]

    results: List[Dict[str, Any]] = []
    base_location = None
    if lat is not None:
        base_location = location_block(
            lat,
            lon,
            token=args.mapbox_token,
            with_map_url=True,
            accuracy_m=fix.get("accuracy_m"),
            provider=fix.get("provider"),
            fallback=fix.get("fallback"),
        )

    for identifier in args.identifiers:
        if len(identifier) == 15 and identifier.isdigit():
            res = decode_imei(identifier, offline=args.offline)
        elif len(identifier) == 8 and identifier.isdigit():
            res = decode_tac(identifier, offline=args.offline)
        else:
            res = {
                "imei": identifier,
                "valid": False,
                "error": "Identifier is neither a 15-digit IMEI nor an 8-digit TAC.",
            }
        if line_block:
            res["line"] = dict(line_block)
        if base_location and res.get("valid"):
            res["location"] = dict(base_location)
            history_key = line_block["e164"] if line_block else res.get("imei")
            if args.record and history_key:
                location = res["location"]
                history.record(
                    history_key,
                    lat=location["lat"],
                    lon=location["lon"],
                    source=location.get("source"),
                    place=location.get("place"),
                )
        history_key = line_block["e164"] if line_block else res.get("imei") or res.get("tac", "")
        if args.status:
            res["last_seen"] = history.status(history_key)
        if args.history:
            res["history"] = history.recent(history_key, limit=args.history_limit)
        results.append(res)

    if line_block and not args.identifiers:
        line_result: Dict[str, Any] = {"imei": None, "valid": True, "line": line_block}
        if base_location:
            line_result["location"] = dict(base_location)
            if args.record:
                location = line_result["location"]
                history.record(
                    line_block["e164"],
                    lat=location["lat"],
                    lon=location["lon"],
                    source=location.get("source"),
                    place=location.get("place"),
                )
        if args.status:
            line_result["last_seen"] = history.status(line_block["e164"])
        if args.history:
            line_result["history"] = history.recent(
                line_block["e164"], limit=args.history_limit
            )
        results.append(line_result)

    if args.map_path:
        try:
            download_static_map(
                lat,
                lon,
                args.map_path,
                token=args.mapbox_token,
                zoom=args.map_zoom,
            )
        except MapboxError as exc:
            print(f"Map error: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for res in results:
            line = res.get("line")
            if line:
                print(f"Line: {line['e164']}")
                print(f"  Country: {line['country']} ({line['region']})")
                print(f"  National: {line['national']}")
                print(f"  Type: {line['line_type']}")
                if line.get("carrier"):
                    print(f"  Carrier: {line['carrier']}")

            if res.get("imei"):
                print(f"IMEI: {res['imei']}")
            if res.get("tac"):
                print(f"  Valid: {res.get('valid')}")
                if not res.get("valid"):
                    print(f"  Error: {res.get('error')}")
                    print()
                    continue

                print(f"  TAC: {res.get('tac')}")
                print(f"  Manufacturer: {res.get('manufacturer')}")
                print(f"  Model: {res.get('model')}")
                print(f"  Region: {res.get('region')}")
                print(f"  Confidence: {res.get('confidence')}")
                print(f"  Likely Original Carrier: {res.get('likely_original_carrier')}")
                print(f"  Device Type: {res.get('device_type')}")
                print(f"  Release Year: {res.get('release_year')}")
            elif not res.get("valid"):
                print(f"  Error: {res.get('error')}")
                print()
                continue

            location = res.get("location")
            if location:
                print(f"  Location: {location['lat']}, {location['lon']}")
                if location.get("accuracy_m") is not None:
                    print(f"  Accuracy: about {location['accuracy_m']} m")
                if location.get("fallback"):
                    print(f"  Fallback: {location['fallback']} (coarse)")
                if location.get("place"):
                    print(f"  Place: {location['place']}")
                if location.get("map_url"):
                    print(f"  Map: {location['map_url']}")
                if location.get("error"):
                    print(f"  Location error: {location['error']}")

            last_seen = res.get("last_seen")
            if last_seen:
                reason = last_seen.get("reason")
                suffix = f" ({reason})" if reason else ""
                print(f"  State: {last_seen['state']}{suffix}")
                if last_seen.get("last_seen"):
                    age_min = last_seen["age_seconds"] // 60
                    print(f"  Last seen: {age_min} min ago")
                    if last_seen.get("last_place"):
                        print(f"  Last place: {last_seen['last_place']}")
                print(f"  Observations: {last_seen.get('observations', 0)}")

            seen_history = res.get("history")
            if seen_history:
                print("  History:")
                for entry in seen_history:
                    stamp = datetime.fromtimestamp(
                        entry.get("ts", 0), tz=timezone.utc
                    ).isoformat(timespec="seconds")
                    place = entry.get("place") or f"{entry.get('lat')}, {entry.get('lon')}"
                    print(f"    - {stamp} {place} [{entry.get('source')}]")

            notes = res.get("notes", [])
            if notes:
                print("  Notes:")
                for n in notes:
                    print(f"    - {n}")

            anomalies = res.get("anomalies", [])
            if anomalies:
                print("  Anomalies:")
                for a in anomalies:
                    print(f"    - [{a.get('severity')}] {a.get('msg')}")

            print()


if __name__ == "__main__":
    main()

