from fastapi import APIRouter, Header, HTTPException, Request
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.security.section_write import require_section_write
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.section_e2ee import present_section_for_api
from app.security.token_resolver import decode_owner_or_nok_token
from app.security.section_file_cleanup import process_section_deleted_files

from .schemas import Section15HealthInformationPayload

router = APIRouter(
    prefix="/sections/section15-health-information",
    tags=["Section 15 – Health Information"],
)

SECTION_ID = "15"
SECTION_KEY = "section15_health_information"
SUBSECTIONS = ["15A", "15B"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section15(
    payload: Section15HealthInformationPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    raw_data = payload.root

    # 🔄 Normalize data
    data = {}

    # 15A (single object)
    if "15A" in raw_data and isinstance(raw_data["15A"], dict):
        data["15A"] = raw_data["15A"]

    # 15B (array)
    if "15B" in raw_data and isinstance(raw_data["15B"], list):
        data["15B"] = raw_data["15B"]

    # 🔥 Delete removed Cloudinary files

    process_section_deleted_files(data, owner['email'])

    encrypted_payload = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        actor=actor,
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 15 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section15(request: Request,
    authorization: str | None = Header(default=None)):
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

    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section)


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section15(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 15 deleted"}
