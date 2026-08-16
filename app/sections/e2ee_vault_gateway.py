"""Unified E2EE vault section read/write — opaque ciphertext only (v3)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Request
from bson import ObjectId

from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.access_control import assert_section_read_access
from app.security.section_e2ee import (
    E2EE_VERSION,
    is_e2ee_write_body,
    present_section_for_api,
)
from app.security.section_write import require_section_write
from app.security.token_resolver import decode_owner_or_nok_token
from app.config import settings

e2ee_vault_router = APIRouter(prefix="/e2ee/vault", tags=["e2ee-vault"])

# slug -> (section_id, section_key, subsections)
# Include FE/legacy path aliases so unlock+migrate never 404 on known sections.
_VAULT_SECTION_CANONICAL: dict[str, tuple[str, str, list[str]]] = {
    "section1-vital-information": ("1", "section1_vitalinformation", ["1A", "1C"]),
    "section5-vehicles": ("5", "section5_vehicles", ["5A", "5B", "5C"]),
    "section6-main-residence": ("6", "section6_main_residence", ["6A"]),
    "section7-insurance-policies": ("7", "section7_insurance", ["7A"]),
    "section8-community-membership": ("8", "section8_community", ["8A"]),
    "section9-charitable-giving": ("9", "section9_charitable", ["9A"]),
    "section10-education-accomplishments": ("10", "section10_education", ["10A"]),
    "section11-military-service": ("11", "section11_military", ["11A"]),
    "section12-banking-financial-accounts": ("12", "section12_banking", ["12A"]),
    "section13-passwords-online-accounts": ("13", "section13_passwords", ["13A"]),
    "section14-investment-accounts": ("14", "section14_investments", ["14A"]),
    "section15-health-information": ("15", "section15_health", ["15A"]),
    "section16-credit-cards-debt": ("16", "section16_credit", ["16A"]),
    "section17-family-treasured-connections": (
        "17",
        "section17_family",
        ["17A"],
    ),
    "section18-employment-business": ("18", "section18_employment", ["18A"]),
    "section19-assets-valuables": ("19", "section19_assets", ["19A"]),
    "section20-legal-document-records": ("20", "section20_legal", ["20A"]),
    "section21-estate-planning-finalwishes": (
        "21",
        "section21_estate",
        ["21A"],
    ),
}

# Aliases used by legacy FastAPI routers / older FE clients.
_VAULT_SECTION_ALIASES: dict[str, str] = {
    "section20-legal-documents-records": "section20-legal-document-records",
    "section21-estate-planning-final-wishes": "section21-estate-planning-finalwishes",
}

VAULT_SECTIONS: dict[str, tuple[str, str, list[str]]] = dict(_VAULT_SECTION_CANONICAL)
for _alias, _canon in _VAULT_SECTION_ALIASES.items():
    VAULT_SECTIONS[_alias] = _VAULT_SECTION_CANONICAL[_canon]

# section_id -> preferred slug (for migration-status)
SECTION_ID_TO_SLUG: dict[str, str] = {
    meta[0]: slug for slug, meta in _VAULT_SECTION_CANONICAL.items()
}


def _require_e2ee_feature() -> None:
    if not getattr(settings, "E2EE_ENABLED", False):
        raise HTTPException(400, "E2EE is disabled on this server")


async def _resolve_reader(request: Request, authorization: str | None, section_id: str):
    decoded = decode_owner_or_nok_token(request, authorization)
    if decoded["role"] == "owner":
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        if not user:
            raise HTTPException(status_code=401)
        owner_id = str(user["_id"])
    elif decoded["role"] == "nextkin":
        user = await users_collection.find_one(
            {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
        )
        if not user:
            raise HTTPException(status_code=401)
        owner_id = user["owner_id"]
    else:
        raise HTTPException(status_code=403)
    assert_section_read_access(user, section_id)
    return owner_id, user


@e2ee_vault_router.get("/{slug}")
async def get_e2ee_section(
    slug: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _require_e2ee_feature()
    meta = VAULT_SECTIONS.get(slug)
    if not meta:
        raise HTTPException(404, "Unknown section")
    section_id, section_key, _ = meta
    owner_id, user = await _resolve_reader(request, authorization, section_id)
    section = await SectionRepository.get(owner_id, section_id)
    decoded = decode_owner_or_nok_token(request, authorization)
    return present_section_for_api(
        owner_id,
        section_id,
        section_key,
        section,
        viewer_role=decoded.get("role"),
    )


@e2ee_vault_router.post("/{slug}")
async def save_e2ee_section(
    slug: str,
    request: Request,
    authorization: str | None = Header(default=None),
    body: dict = Body(...),
):
    _require_e2ee_feature()
    meta = VAULT_SECTIONS.get(slug)
    if not meta:
        raise HTTPException(404, "Unknown section")
    section_id, section_key, subsections = meta

    if not is_e2ee_write_body(body):
        raise HTTPException(
            400,
            "E2EE save requires { e2ee: true, ciphertext: string }",
        )

    owner, actor = await require_section_write(request, authorization, section_id)
    if not owner:
        raise HTTPException(status_code=401)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        actor=actor,
        section_id=section_id,
        section_key=section_key,
        encrypted_data=str(body["ciphertext"]),
        subsections=subsections,
        encryption_version=E2EE_VERSION,
        plaintext=None,
        source="e2ee",
    )
    return {
        "message": f"Section {section_id} saved (E2EE)",
        "encryption_version": E2EE_VERSION,
        "e2ee": True,
    }
