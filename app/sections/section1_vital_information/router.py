from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request
from bson import ObjectId

from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.sections.section1_vital_information.schemas import Section1VitalInformationPayload
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.section_e2ee import present_section_for_api
from app.security.token_resolver import decode_owner_or_nok_token
from app.security.access_control import assert_section_read_access
from app.security.section_write import require_section_write

router = APIRouter(
    prefix="/sections/section1-vital-information",
    tags=["Section 1 – Vital Information"],
)

SECTION_ID = "1"
SECTION_KEY = "section1_vitalinformation"
SUBSECTIONS = ["1A", "1C"]


@router.post("")
async def save_section1(
    payload: Section1VitalInformationPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    encrypted = encrypt_section_data(str(owner["_id"]), SECTION_ID, payload.dict())

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        actor=actor,
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    legal_name = (payload.vital_info or {}).get("full_legal_name")
    from app.ai.semantic_field_map import as_plain_text
    from app.auth.ssdmf import persist_identity_snapshot

    name_text = as_plain_text(legal_name) or ""
    if name_text:
        await users_collection.update_one(
            {"_id": owner["_id"]},
            {
                "$set": {
                    "full_name": name_text,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
    await persist_identity_snapshot(
        owner["_id"],
        payload.vital_info,
        full_name_fallback=name_text or str(owner.get("full_name") or ""),
    )

    return {"message": "Section 1 saved"}


@router.get("")
async def get_section1(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_owner_or_nok_token(request, authorization)

    # OWNER
    if decoded["role"] == "owner":
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        if not user:
            raise HTTPException(status_code=401)
        owner_id = str(user["_id"])

    # NEXT-OF-KIN
    elif decoded["role"] == "nextkin":
        user = await users_collection.find_one(
            {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
        )
        if not user:
            raise HTTPException(status_code=401)
        owner_id = user["owner_id"]

    else:
        raise HTTPException(status_code=403)

    # 🔐 enforce access
    assert_section_read_access(user, SECTION_ID)

    section = await SectionRepository.get(owner_id, SECTION_ID)
    if not section:
        return {}

    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section, viewer_role=decoded.get("role"))


@router.delete("")
async def delete_section1(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner",
    })

    await SectionRepository.delete(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
    )

    return {"message": "Section 1 deleted"}
