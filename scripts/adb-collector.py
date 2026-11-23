#!/usr/bin/env python3
"""
ADB collector: pull a radio snapshot over USB and post it. Nothing is
installed on the phone; only Developer Options -> USB debugging is needed.

Usage:
    VAULTTRACE_URL=http://127.0.0.1:8765 VAULTTRACE_TOKEN=... \
    VAULTTRACE_IMEI=354265802012527 python adb-collector.py --interval 120
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request

CELL_RE = re.compile(
    r"CellIdentity(Gsm|Wcdma|Lte|Nr):\{(.*?)\}", re.DOTALL
)
RADIO = {"Gsm": "gsm", "Wcdma": "wcdma", "Lte": "lte", "Nr": "nr"}
MAC_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
CTX = ssl._create_unverified_context()


def adb(*args):
    return subprocess.check_output(["adb", *args], text=True, stderr=subprocess.DEVNULL)


def _field(body, *names):
    for name in names:
        match = re.search(re.escape(name) + r"\s*=\s*(0x[0-9a-fA-F]+|\d+)", body)
        if match:
            return int(match.group(1), 0)
    return None


def cells():
    try:
        dump = adb("shell", "dumpsys", "telephony.registry")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"adb telephony failed: {exc}", file=sys.stderr)
        return []

    result = []
    for cell_type, body in CELL_RE.findall(dump):
        entry = {
            "radioType": RADIO[cell_type],
            "mobileCountryCode": _field(body, "mMcc"),
            "mobileNetworkCode": _field(body, "mMnc"),
            "locationAreaCode": _field(body, "mLac", "mTac"),
            "cellId": _field(body, "mCi", "mCid"),
        }
        if None in entry.values():
            continue
        result.append(entry)
    return result


def wifi():
    try:
        dump = adb("shell", "cmd", "wifi", "list-scan-results")
    except (OSError, subprocess.CalledProcessError):
        return []

    result = []
    for line in dump.splitlines():
        mac = MAC_RE.search(line)
        if not mac:
            continue
        entry = {"macAddress": mac.group(0)}
        level = re.search(r"(-?\d+)", line[mac.end():])
        if level:
            entry["signalStrength"] = int(level.group(1))
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
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not (args.url and args.token and args.imei):
        parser.error("--url, --token and --imei are required")

    while True:
        payload = {"cellTowers": cells(), "wifiAccessPoints": wifi()}
        if not payload["cellTowers"] and not payload["wifiAccessPoints"]:
            print("no cells or WiFi parsed")
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
