# app/auth/phone.py
import re

import phonenumbers


def _normalize_raw_phone(phone: str) -> str:
    raw = str(phone).strip()
    if not raw:
        return raw

    if raw.startswith("+"):
        return raw

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw

    return f"+{digits}"


def format_phone(phone: str, default_region: str = "US") -> str:
    if not phone or not str(phone).strip():
        raise ValueError("Phone number is required")

    raw = _normalize_raw_phone(phone)

    parse_attempts = [(raw, None), (raw, default_region), (str(phone).strip(), default_region)]

    last_error: phonenumbers.NumberParseException | None = None

    for candidate, region in parse_attempts:
        if not candidate:
            continue

        try:
            parsed = phonenumbers.parse(candidate, region)

            if not phonenumbers.is_valid_number(parsed):
                continue

            return phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164,
            )
        except phonenumbers.NumberParseException as exc:
            last_error = exc
            continue

    if last_error:
        raise ValueError(
            "Invalid phone format. Select your country code and enter a valid phone number."
        ) from last_error

    raise ValueError(
        "Invalid phone format. Select your country code and enter a valid phone number."
    )
