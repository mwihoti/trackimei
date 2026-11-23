#!/usr/bin/env python3
"""
Termux collector: read the phone's visible cells and WiFi, POST them to
`vaulttrace-imei --serve`, and repeat.

Requirements (inside Termux):
    pkg install python termux-api
    # install the Termux:API app and grant Location permission

Usage:
    VAULTTRACE_URL=http://192.168.1.10:8765 VAULTTRACE_TOKEN=... \
    VAULTTRACE_IMEI=354265802012527 python termux-collector.py --interval 300
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request

RADIO = {"gsm": "gsm", "lte": "lte", "wcdma": "wcdma", "nr": "nr", "tdscdma": "gsm"}
CTX = ssl._create_unverified_context()


def run_json(command):
    try:
        output = subprocess.check_output(command, stderr=subprocess.DEVNULL)
        return json.loads(output or b"[]")
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"{command[0]} failed: {exc}", file=sys.stderr)
        return []


def cells():
    result = []
    for cell in run_json(["termux-telephony-cellinfo"]):
        radio = RADIO.get((cell.get("type") or "").lower())
        if not radio or cell.get("registered") is False:
            continue
        entry = {
            "radioType": radio,
            "mobileCountryCode": cell.get("mcc"),
            "mobileNetworkCode": cell.get("mnc"),
            "locationAreaCode": cell.get("lac") or cell.get("tac"),
            "cellId": cell.get("cid") or cell.get("ci"),
        }
        if None in entry.values():
            continue
        if cell.get("signalStrength") is not None:
            entry["signalStrength"] = cell["signalStrength"]
        result.append(entry)
    return result


def wifi():
    result = []
    for ap in run_json(["termux-wifi-scaninfo"]):
        if not ap.get("bssid"):
            continue
        entry = {"macAddress": ap["bssid"]}
        if ap.get("level") is not None:
            entry["signalStrength"] = ap["level"]
        result.append(entry)
    return result


def post(url, token, imei, payload):
    request = urllib.request.Request(
        url.rstrip("/") + "/snapshot?imei=" + imei,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": token},
    )
    with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
        return json.loads(response.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("VAULTTRACE_URL"))
    parser.add_argument("--token", default=os.environ.get("VAULTTRACE_TOKEN"))
    parser.add_argument("--imei", default=os.environ.get("VAULTTRACE_IMEI"))
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if not (args.url and args.token and args.imei):
        parser.error("--url, --token and --imei are required")

    while True:
        payload = {"cellTowers": cells(), "wifiAccessPoints": wifi()}
        if not payload["cellTowers"] and not payload["wifiAccessPoints"]:
            print("no cells or WiFi visible")
        else:
            try:
                data = post(args.url, args.token, args.imei, payload)
                location = data.get("location") or {}
                print(
                    time.strftime("%H:%M:%S"),
                    location.get("lat"),
                    location.get("lon"),
                    location.get("place"),
                    f"±{location.get('accuracy_m')}m",
                )
            except Exception as exc:
                print(f"post failed: {exc}", file=sys.stderr)

        if args.once:
            return
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
