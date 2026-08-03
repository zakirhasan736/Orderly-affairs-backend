"""
Family collaborator create/update schemas (Vault Settings — separate from NOK).
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator


class FamilyCreateRequest(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: str | None = None
    # Full Dashboard Access | Area-Specific Access (legacy Full Kit / Section-Specific accepted)
    access_level: str = "Full Dashboard Access"
    authorized_sections: list[str] | None = []
    portal_role: str = "viewer"
    dashboard_permissions: dict[str, bool] | None = None
    master_password: str | None = None

    @field_validator("full_name", "relationship")
    @classmethod
    def required_non_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("This field is required")
        return cleaned


class FamilyUpdateRequest(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    relationship: str | None = None
    phone_number: str | None = None
    access_level: str | None = None
    authorized_sections: list[str] | None = None
    portal_role: str | None = None
    dashboard_permissions: dict[str, bool] | None = None
    master_password: str | None = None
