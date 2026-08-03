"""
Next-of-Kin create request schema with required-field validation.
Kept separate from routes so unit tests can import without loading the full app.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator


class NextKinCreateRequest(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: str | None = None
    access_level: str = "Full Kit Access"
    authorized_sections: list[str] | None = []
    # Ignored for Next-of-Kin (always read-only). Kept optional for older clients.
    portal_role: str | None = None
    immediate_access: bool | None = False
    nok_letter_received: bool | None = False
    master_password: str | None = None
    password_card_generated: bool | None = False
    card_storage_location: str | None = None
    key_bag_location: str | None = None
    documents_bag_location: str | None = None
    special_instructions: str | None = None

    @field_validator("full_name", "relationship")
    @classmethod
    def required_non_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("This field is required")
        return cleaned
