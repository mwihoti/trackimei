"""
theft.py
--------
Local device registration, theft reporting and recovery state tracking.

Nothing here blocks an IMEI or locates a phone. It preserves the facts a
carrier, police officer or insurer will ask for, and keeps an auditable
timeline of what the owner has already done.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import history

DATA_DIR = os.environ.get("VAULTTRACE_HOME") or os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "vaulttrace-imei",
)
DEVICE_DIR = os.path.join(DATA_DIR, "devices")

STATES = ("owned", "reported", "sim-blocked", "imei-blocked", "recovered")
ACTIONS = {
    "reported": "File the police report and obtain the abstract/OB number.",
    "sim-blocked": "Ask the carrier to block the SIM and reissue it.",
    "imei-blocked": "Ask the carrier to blacklist the IMEI in the EIR.",
    "recovered": "Verify the device, then ask the carrier to lift the block.",
}
FIELDS = ("owner", "serial", "purchase_ref", "carrier", "msisdn", "notes")


class TheftError(RuntimeError):
    pass


def _path(imei: str) -> str:
    return os.path.join(DEVICE_DIR, f"{imei}.json")


def _stamp(value: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(value or time.time(), tz=timezone.utc)
    return moment.isoformat(timespec="seconds")


def load(imei: str) -> Optional[Dict]:
    try:
        with open(_path(imei), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def register(imei: str, **fields) -> Dict:
    existing = load(imei) or {}
    record = {
        "imei": imei,
        "registered": existing.get("registered") or time.time(),
        "state": existing.get("state") or "owned",
        "events": existing.get("events") or [],
    }
    for name in FIELDS:
        if fields.get(name) is not None:
            record[name] = fields[name]
        elif name in existing:
            record[name] = existing[name]

    if not record["events"]:
        record["events"].append(
            {"ts": record["registered"], "state": "owned", "note": "registered"}
        )

    os.makedirs(DEVICE_DIR, exist_ok=True)
    with open(_path(imei), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return record


def advance(imei: str, target: str, note: Optional[str] = None) -> Dict:
    if target not in STATES:
        raise TheftError(f"Unknown state '{target}'. Choose: {', '.join(STATES)}.")
    record = load(imei)
    if record is None:
        raise TheftError("Device is not registered; run --register first.")

    current = record.get("state", "owned")
    if STATES.index(target) < STATES.index(current):
        raise TheftError(f"Cannot move back from '{current}' to '{target}'.")
    if target == current:
        raise TheftError(f"Device is already '{current}'.")

    record["state"] = target
    record["events"].append({"ts": time.time(), "state": target, "note": note})
    with open(_path(imei), "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return record


def reference(record: Dict) -> str:
    seed = f"{record['imei']}:{record.get('registered')}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"VT-{digest}"


def checklist(record: Dict) -> List[str]:
    reached = STATES.index(record.get("state", "owned"))
    lines = []
    for index, state in enumerate(STATES[1:], start=1):
        mark = "x" if index <= reached else " "
        lines.append(f"- [{mark}] **{state}** — {ACTIONS[state]}")
    return lines


def report(imei: str, profile: Dict) -> str:
    record = load(imei)
    if record is None:
        raise TheftError("Device is not registered; run --register first.")

    ref = reference(record)
    events = record.get("events", [])
    seen = history.recent(imei, limit=10)

    lines = [
        f"# Theft report {ref}",
        "",
        f"Generated: {_stamp()}",
        f"State: **{record.get('state', 'owned')}**",
        "",
        "## Device",
        "",
        f"- IMEI: {imei}",
        f"- TAC: {profile.get('tac', 'Unknown')}",
        f"- Manufacturer: {profile.get('manufacturer', 'Unknown')}",
        f"- Model: {profile.get('model', 'Unknown')}",
        f"- Device type: {profile.get('device_type', 'Unknown')}",
        f"- Release year: {profile.get('release_year', 'Unknown')}",
        f"- Serial (if recorded): {record.get('serial', 'not recorded')}",
        f"- Purchase reference: {record.get('purchase_ref', 'not recorded')}",
        "",
        "## Owner and line",
        "",
        f"- Owner: {record.get('owner', 'not recorded')}",
        f"- Carrier: {record.get('carrier', 'not recorded')}",
        f"- MSISDN: {record.get('msisdn', 'not recorded')}",
        f"- Notes: {record.get('notes', 'none')}",
        "",
        "## Timeline",
        "",
    ]
    for event in events:
        note = event.get("note") or ""
        lines.append(f"- {_stamp(event.get('ts'))} {event.get('state')} {note}".rstrip())

    lines += ["", "## Checklist", ""]
    lines += checklist(record)

    lines += ["", "## Last known observations", ""]
    if seen:
        for entry in seen:
            place = entry.get("place") or f"{entry.get('lat')}, {entry.get('lon')}"
            lines.append(
                f"- {_stamp(entry.get('ts'))} {place} [{entry.get('source')}]"
            )
    else:
        lines.append("- No local observations recorded.")

    lines += [
        "",
        "## Caveats",
        "",
        "- IMEI identifies the hardware, not the line or the person.",
        "- Observations are only as recent as the last recorded snapshot.",
        "- An IMEI block must be applied by the carrier/EIR; this report does not block it.",
        "- Keep the police abstract number with this report for insurer and carrier use.",
        "",
    ]
    return "\n".join(lines)


def status_text(record: Dict) -> str:
    ref = reference(record)
    state = record.get("state", "owned")
    lines = [f"Reference: {ref}", f"State: {state}"]
    nxt = STATES.index(state) + 1
    if nxt < len(STATES):
        upcoming = STATES[nxt]
        lines.append(f"Next: {upcoming} — {ACTIONS[upcoming]}")
    else:
        lines.append("Recovery complete.")
    lines.append("Checklist:")
    lines.extend(checklist(record))
    return "\n".join(lines)
