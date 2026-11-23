"""
phone.py
--------
MSISDN (line) parsing and offline line-type/carrier profiling.

An IMEI identifies hardware and an MSISDN identifies a subscription. Neither
can be derived from the other. This module only profiles a number you supply.
"""

import re
from typing import Dict, Optional

try:
    import phonenumbers
    from phonenumbers import (
        NumberParseException,
        PhoneNumberFormat,
        PhoneNumberType,
        carrier,
    )
except ImportError:
    phonenumbers = None


class NumberError(ValueError):
    pass


TYPE_NAMES = {
    "MOBILE": "mobile",
    "FIXED_LINE": "fixed line",
    "FIXED_LINE_OR_MOBILE": "fixed or mobile",
    "TOLL_FREE": "toll free",
    "PREMIUM_RATE": "premium rate",
    "SHARED_COST": "shared cost",
    "VOIP": "voip",
    "PERSONAL_NUMBER": "personal number",
    "PAGER": "pager",
    "UAN": "uan",
    "VOICEMAIL": "voicemail",
    "UNKNOWN": "unknown",
}

CALLING_CODES = {
    "254": ("KE", "Kenya"),
    "255": ("TZ", "Tanzania"),
    "256": ("UG", "Uganda"),
    "250": ("RW", "Rwanda"),
    "27": ("ZA", "South Africa"),
    "234": ("NG", "Nigeria"),
    "233": ("GH", "Ghana"),
    "20": ("EG", "Egypt"),
    "212": ("MA", "Morocco"),
    "1": ("US", "United States / Canada"),
    "44": ("GB", "United Kingdom"),
    "353": ("IE", "Ireland"),
    "33": ("FR", "France"),
    "49": ("DE", "Germany"),
    "34": ("ES", "Spain"),
    "39": ("IT", "Italy"),
    "31": ("NL", "Netherlands"),
    "32": ("BE", "Belgium"),
    "41": ("CH", "Switzerland"),
    "43": ("AT", "Austria"),
    "46": ("SE", "Sweden"),
    "47": ("NO", "Norway"),
    "45": ("DK", "Denmark"),
    "358": ("FI", "Finland"),
    "48": ("PL", "Poland"),
    "351": ("PT", "Portugal"),
    "30": ("GR", "Greece"),
    "90": ("TR", "Turkey"),
    "7": ("RU", "Russia / Kazakhstan"),
    "380": ("UA", "Ukraine"),
    "971": ("AE", "United Arab Emirates"),
    "966": ("SA", "Saudi Arabia"),
    "972": ("IL", "Israel"),
    "91": ("IN", "India"),
    "92": ("PK", "Pakistan"),
    "880": ("BD", "Bangladesh"),
    "86": ("CN", "China"),
    "81": ("JP", "Japan"),
    "82": ("KR", "South Korea"),
    "65": ("SG", "Singapore"),
    "60": ("MY", "Malaysia"),
    "62": ("ID", "Indonesia"),
    "66": ("TH", "Thailand"),
    "84": ("VN", "Vietnam"),
    "63": ("PH", "Philippines"),
    "61": ("AU", "Australia"),
    "64": ("NZ", "New Zealand"),
    "55": ("BR", "Brazil"),
    "54": ("AR", "Argentina"),
    "52": ("MX", "Mexico"),
    "56": ("CL", "Chile"),
    "57": ("CO", "Colombia"),
}


def _normalize(raw: str) -> str:
    value = raw.strip().replace(" ", "").replace("-", "").replace("(", "")
    value = value.replace(")", "")
    if value.startswith("00"):
        value = "+" + value[2:]
    return value


def _fallback_parse(value: str, region: str) -> Dict:
    digits = value.lstrip("+")
    if not digits.isdigit() or not 8 <= len(digits) <= 15:
        raise NumberError(f"'{value}' is not a valid E.164 number.")

    if not value.startswith("+"):
        if region == "KE" and digits.startswith("0"):
            digits = "254" + digits[1:]
        elif region == "KE":
            digits = "254" + digits
        else:
            raise NumberError("Use +country code for non-Kenyan numbers.")

    for length in (3, 2, 1):
        code = digits[:length]
        if code in CALLING_CODES:
            iso, country = CALLING_CODES[code]
            national = digits[length:]
            return {
                "e164": "+" + digits,
                "national": national,
                "region": iso,
                "country": country,
                "line_type": "unknown",
                "carrier": None,
            }
    raise NumberError(f"Unknown country calling code in '+{digits}'.")


def parse_number(raw: str, region: str = "KE") -> Dict:
    value = _normalize(raw)
    if phonenumbers is None:
        return _fallback_parse(value, region)

    try:
        number = phonenumbers.parse(value, region)
    except NumberParseException as exc:
        raise NumberError(f"Could not parse '{value}': {exc}") from exc

    if not phonenumbers.is_valid_number(number):
        raise NumberError(f"'{value}' is not a valid number.")

    type_name = TYPE_NAMES.get(
        PhoneNumberType.to_string(phonenumbers.number_type(number)).upper(),
        "unknown",
    )
    iso = phonenumbers.region_code_for_number(number)
    country = iso
    for code, (code_iso, code_country) in CALLING_CODES.items():
        if code_iso == iso:
            country = code_country
            break

    return {
        "e164": phonenumbers.format_number(number, PhoneNumberFormat.E164),
        "national": phonenumbers.format_number(number, PhoneNumberFormat.NATIONAL),
        "region": iso,
        "country": country,
        "line_type": type_name,
        "carrier": carrier.name_for_number(number, "en") or None,
    }
