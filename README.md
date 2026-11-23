<p align="center">
  <img src="https://img.shields.io/badge/status-active-success.svg" />
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" />
  <img src="https://img.shields.io/badge/python-3.10+-yellow.svg" />
  <img src="https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg" />
  <img src="https://img.shields.io/badge/imei-analysis-critical.svg" />
</p>

# VaultTrace IMEI

VaultTrace IMEI is a focused utility for analyzing IMEI identifiers and correlating TAC values with device intelligence. The tool is built for OSINT practitioners, forensic analysts, telecom researchers, and fraud investigators who need reliable IMEI interpretation.

## Overview

An IMEI contains a TAC block that identifies the device family. VaultTrace IMEI extracts the TAC, validates the IMEI structure, and maps the TAC to manufacturer, model, region, device type, and release year. It also performs anomaly checks to highlight irregular or suspicious identifiers.

An IMEI is a hardware identity number and does not contain coordinates. Location is only added when you supply the device's last known position; Mapbox is then used to reverse-geocode the coordinates and render a map.

## Features

- Full IMEI parsing and Luhn checksum validation
- TAC extraction and lookup against a local cache with public GitHub fallback
- Manufacturer, model, region, device type, and release year identification
- Confidence scoring for TAC matches
- Optional carrier inference when available
- Optional location block with Mapbox reverse geocoding and static maps
- Structured JSON output for automation and ingestion
- Basic anomaly detection with severity levels
- CLI interface for single or multiple identifiers

## Installation

Clone the repository and install in editable mode:

```bash
git clone https://github.com/theosintvault/vaulttrace-imei.git
cd vaulttrace-imei
pip install -e .
```

## Usage

Decode one or more IMEIs or 8-digit TACs:

```bash
vaulttrace-imei 359103740000004
vaulttrace-imei 359103740000004 35693803 --json
python -m vaulttrace_imei 359103740000004
```

Unknown TACs are looked up in the public dataset and cached locally. Add
`--offline` to skip the network and use local data only.

## Location

Pass the device's last known coordinates with `--lat` and `--lon`.

Put the Mapbox token in a `.env` file at the repository root (copy
`.env.example`) or export it:

```bash
cp .env.example .env
# .env: MAPBOX_TOKEN=pk.your_token_here
vaulttrace-imei 359103740000004 --lat -1.2921 --lon 36.8219
```

`MAPBOX_TOKEN` and `--mapbox-token pk...` are also accepted, with the flag
taking precedence over the file and environment.

JSON output gains a `location` block with the coordinates and resolved `place`. Write a static map image as well with `--map`:

```bash
vaulttrace-imei 359103740000004 --lat -1.2921 --lon 36.8219 --map device.png
```

Without a token, coordinates are still recorded but geocoding and map output are skipped.

### Automatic geolocation

If you have a radio snapshot from the device, `--locate` resolves coordinates
for you:

```bash
python -m vaulttrace_imei 354265802197724 --locate --cell 639,2,504,12345,-70 --wifi aa:bb:cc:dd:ee:ff,-50
python -m vaulttrace_imei 354265802197724 --locate --snapshot snapshot.json --map device.png
```

The snapshot file takes the same `cellTowers` / `wifiAccessPoints` shape as
Android's `getAllCellInfo()` and `getScanResults()`. The default provider is
keyless beaconDB; use `--provider google --google-key ...` for Google, which
usually has better cell coverage.

Accuracy depends on the input: WiFi gives tens of metres, a single cell tower
can be kilometres, and an IP fallback is about 25 km.

### Activity and last seen

Record an observation whenever you have a fresh position, then ask for the
inferred state later:

```bash
python -m vaulttrace_imei 354265802197724 --locate --snapshot snapshot.json --record
python -m vaulttrace_imei 354265802197724 --status
python -m vaulttrace_imei 354265802197724 --history
```

History is stored in `~/.local/share/vaulttrace-imei/history.jsonl` (override
with `VAULTTRACE_HOME`). States are `active` under 15 minutes, `recent` under
6 hours, `stale` under 48 hours, and `dormant` beyond that.

This is inference, not a power-state read. An IMEI carries no on/off bit, and
the network routes by IMSI. A dormant record can mean powered off, airplane
mode, no coverage, or simply that nobody ran a snapshot. For network-side
status you need an HLR/SS7 lookup against the MSISDN, which can report
attached/roaming but still not reliably "off".

### Phone numbers

An IMEI is the device and an MSISDN is the line; one does not derive the
other. Profile a number alongside the device, or on its own:

```bash
python -m vaulttrace_imei 354265802197724 --msisdn +254712345678
python -m vaulttrace_imei --msisdn 0712345678 --status
```

Output covers E.164 form, country, national format, line type, and carrier,
using an offline `phonenumbers` lookup. National numbers use `--region`
(default `KE`) unless they include a `+` country code. `--record`, `--status`,
and `--history` key on the number when `--msisdn` is given.

This does not return an owner name or live attachment. Those need a lookup
service: caller-name APIs for ownership, and an HLR/SS7 lookup for
attached/roaming state.

### Live collection from the phone

Repeated real fixes need the phone to report. Start an ingest server on a
machine on the same network:

```bash
export VAULTTRACE_SERVER_TOKEN=$(openssl rand -hex 16)
python -m vaulttrace_imei --serve --host 0.0.0.0 --port 8765
```

On the phone, install Termux plus the Termux:API app, then:

```bash
pkg install python termux-api
termux-wake-lock
VAULTTRACE_URL=https://<laptop-lan-ip>:8765 VAULTTRACE_TOKEN=... \
VAULTTRACE_IMEI=354265802012527 \
python scripts/termux-collector.py --interval 300
```

Grant the Termux:API app Location permission and turn WiFi scanning on; the
script reads `termux-telephony-cellinfo` and `termux-wifi-scaninfo`, posts every
interval, and prints the resolved place and accuracy. Each POST is recorded, so
`GET /status?imei=...` and the CLI's `--status` become genuine.

The token is mandatory (`X-Token: ...`). Binding `0.0.0.0` exposes the ingest
port to the LAN, so use a long random token.

### Without installing anything on the phone

**Browser route.** Browsers only expose geolocation on a secure origin, so run
the server with a temporary self-signed certificate:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
  -days 30 -subj "/CN=vaulttrace" -addext "subjectAltName=IP:192.168.2.2"
python -m vaulttrace_imei --serve --host 0.0.0.0 --port 8765 --cert cert.pem --key key.pem
```

Open `https://192.168.2.2:8765/?token=<token>&imei=<imei>` in the phone
browser, accept the certificate warning, and press the button. Keep the tab in
the foreground; the page uses `watchPosition` and posts each fix. This is
GPS-grade, no app required.

**ADB route.** Enable Developer Options → USB debugging, connect USB, then:

```bash
python scripts/adb-collector.py --url https://127.0.0.1:8765 --token ... --imei ...
```

The host-side script shells out to `adb shell dumpsys telephony.registry` and
`cmd wifi list-scan-results`. Nothing is installed on the phone.

## Library

```python
from vaulttrace_imei import decode_imei

decode_imei("359103740000004")
```

## Data Sources

- TAC data: local `tac_db.json` cache, fetched on demand from the public
  [MoazEb/tac-database](https://github.com/MoazEb/tac-database) CSV dataset.
- The dataset CSV is cached at `vaulttrace_imei/tac_full.csv` for a week.
- Location enrichment: Mapbox Geocoding and Static Images APIs.

## License

MIT
