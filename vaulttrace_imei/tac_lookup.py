"""
tac_lookup.py
-------------
Public-safe TAC lookup module.

- No private TAC/device data is shipped.
- Uses public GitHub TAC DB as primary source.
- Optional full DB preload (--update-cache).
- Caches results locally in tac_db.json.
"""

import json
import os
import re
import requests
import csv
import time
from typing import Dict, Optional

# -----------------------------
# Config
# -----------------------------
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "tac_db.json")
CSV_CACHE_PATH = os.path.join(os.path.dirname(__file__), "tac_full.csv")
GITHUB_TAC_BASE = "https://raw.githubusercontent.com/MoazEb/tac-database/main/tac"
GITHUB_TAC_CSV = "https://raw.githubusercontent.com/MoazEb/tac-database/main/tac_full.csv"
HTTP_TIMEOUT = 5  # seconds
CSV_MAX_AGE = 7 * 24 * 3600

# -----------------------------
# Local DB Handling
# -----------------------------
def load_tac_db() -> Dict[str, dict]:
    if not os.path.exists(LOCAL_DB_PATH):
        return {}
    try:
        with open(LOCAL_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_tac_db(db: Dict[str, dict]) -> None:
    try:
        with open(LOCAL_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except OSError:
        pass

TAC_DB = load_tac_db()

# -----------------------------
# Online Sources
# -----------------------------
def _download_csv() -> bool:
    try:
        response = requests.get(GITHUB_TAC_CSV, timeout=60)
        if response.status_code != 200:
            return False
        tmp_path = CSV_CACHE_PATH + ".tmp"
        with open(tmp_path, "wb") as handle:
            handle.write(response.content)
        os.replace(tmp_path, CSV_CACHE_PATH)
        return True
    except (requests.RequestException, OSError):
        return False


def _csv_is_stale() -> bool:
    try:
        return time.time() - os.path.getmtime(CSV_CACHE_PATH) > CSV_MAX_AGE
    except OSError:
        return True


def _parse_specs(brand: str, specs: str) -> dict:
    parts = [part.strip() for part in specs.split(",") if part.strip()]
    model = parts[0] if parts else "Unknown"
    year = "Unknown"
    for part in reversed(parts):
        if re.fullmatch(r"(19|20)\d{2}", part):
            year = int(part)
            break

    return {
        "manufacturer": brand.title() if brand.isupper() else brand,
        "model": model,
        "region": "Global",
        "release_year": year,
        "device_type": "Smartphone",
        "confidence": 0.85,
        "notes": [f"TAC match from public dataset: {specs}"],
    }


def fetch_tac_from_github(tac: str) -> Optional[dict]:
    """Fetch TAC details from the public GitHub TAC dataset."""
    try:
        url = f"{GITHUB_TAC_BASE}/{tac}.json"
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass

    if not os.path.exists(CSV_CACHE_PATH) or _csv_is_stale():
        _download_csv()
    if not os.path.exists(CSV_CACHE_PATH):
        return None

    try:
        with open(CSV_CACHE_PATH, "r", encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) >= 3 and row[1].strip() == tac:
                    return _parse_specs(row[0].strip(), row[2].strip())
    except OSError:
        return None
    return None

# -----------------------------
# Main Lookup
# -----------------------------
def basic_tac_profile(identifier: str) -> dict:
    """Return TAC profile from TAC or IMEI."""
    if re.fullmatch(r"\d{15}", identifier):
        tac = identifier[:8]
    elif re.fullmatch(r"\d{8}", identifier):
        tac = identifier
    else:
        raise ValueError("Invalid input. Must be 8-digit TAC or 15-digit IMEI.")

    profile = TAC_DB.get(tac, {
        "tac": tac,
        "manufacturer": "Unknown",
        "model": "Unknown",
        "device_type": "Unknown",
        "region": "Unknown"
    })

    if profile["manufacturer"] == "Unknown":
        gh_data = fetch_tac_from_github(tac)
        if gh_data:
            profile.update(gh_data)
            TAC_DB[tac] = profile
            save_tac_db(TAC_DB)

    return profile

# -----------------------------
# Cache Management
# -----------------------------
def clear_tac_cache() -> None:
    global TAC_DB
    TAC_DB = {}
    save_tac_db(TAC_DB)

def list_cached_tacs() -> Dict[str, dict]:
    return dict(TAC_DB)

def update_local_cache_from_github() -> None:
    """Download the full TAC dataset from GitHub and merge it into local cache."""
    if not _download_csv():
        print("Failed to download TAC CSV.")
        return

    count = 0
    try:
        with open(CSV_CACHE_PATH, "r", encoding="utf-8", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 3 or not row[1].strip().isdigit():
                    continue
                TAC_DB[row[1].strip()] = _parse_specs(row[0].strip(), row[2].strip())
                count += 1
    except OSError:
        print("Failed to read TAC CSV.")
        return

    save_tac_db(TAC_DB)
    print(f"Updated local TAC cache with {count} entries from GitHub.")

# -----------------------------
# CLI Test
# -----------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--update-cache":
        update_local_cache_from_github()
    elif len(sys.argv) > 1:
        print(json.dumps(basic_tac_profile(sys.argv[1]), indent=2))
    else:
        print("Usage: python tac_lookup.py <IMEI|TAC> or --update-cache")
