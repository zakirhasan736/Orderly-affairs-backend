# app/auth/phone.py
import phonenumbers

def format_phone(phone: str) -> str:
    if not phone or not str(phone).strip():
        raise ValueError("Phone number is required")

    raw = str(phone).strip()

    try:
        parsed = phonenumbers.parse(raw, None)

        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Invalid phone number")

        return phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )

    except phonenumbers.NumberParseException:
        raise ValueError(
            "Invalid phone format. Use full international format like +8801XXXXXXXXX"
        )