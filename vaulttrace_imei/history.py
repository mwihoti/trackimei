"""
history.py
----------
Local observation log used to infer device activity from the last time a
snapshot was seen.

Power state cannot be read from an IMEI. This is inference only: an old
observation suggests the device is off, out of coverage, or no longer being
reported. It cannot prove any of those.
"""

import json
import os
import time
from typing import Dict, List, Optional

DATA_DIR = os.environ.get("VAULTTRACE_HOME") or os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "vaulttrace-imei",
)
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")

ACTIVE_SECONDS = 15 * 60
RECENT_SECONDS = 6 * 3600
STALE_SECONDS = 48 * 3600


def record(
    imei: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    source: Optional[str] = None,
    place: Optional[str] = None,
    when: Optional[float] = None,
) -> Dict:
    observation = {
        "imei": imei,
        "ts": when if when is not None else time.time(),
        "lat": lat,
        "lon": lon,
        "source": source,
        "place": place,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation) + "\n")
    return observation


def observations(imei: str) -> List[Dict]:
    entries: List[Dict] = []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("imei") == imei:
                    entries.append(entry)
    except OSError:
        pass
    return entries


def recent(imei: str, limit: int = 10) -> List[Dict]:
    entries = sorted(
        observations(imei), key=lambda entry: entry.get("ts", 0), reverse=True
    )
    return entries[:limit]


def status(imei: str) -> Dict:
    entries = observations(imei)
    if not entries:
        return {
            "state": "unknown",
            "reason": "no local observations",
            "observations": 0,
        }

    latest = max(entries, key=lambda entry: entry.get("ts", 0))
    age = max(0, time.time() - latest.get("ts", 0))
    if age <= ACTIVE_SECONDS:
        state = "active"
    elif age <= RECENT_SECONDS:
        state = "recent"
    elif age <= STALE_SECONDS:
        state = "stale"
    else:
        state = "dormant"

    return {
        "state": state,
        "age_seconds": int(age),
        "last_seen": latest.get("ts"),
        "observations": len(entries),
        "last_place": latest.get("place"),
        "source": latest.get("source"),
    }
