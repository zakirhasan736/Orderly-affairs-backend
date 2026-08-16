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

from .schemas import Section21EstatePlanningPayload

# Matches production / committed FastAPI slug (hyphenated final-wishes).
router = APIRouter(
    prefix="/sections/section21-estate-planning-final-wishes",
    tags=["Section 21 – Estate Planning & Final Wishes"],
)

SECTION_ID = "21"
SECTION_KEY = "section21_estate_planning_final_wishes"
SUBSECTIONS = ["21A", "21B", "21C"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section21(
    payload: Section21EstatePlanningPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    raw = payload.root
    data = {}

    for key in ["21A", "21B", "21C"]:
        if isinstance(raw.get(key), dict):
            data[key] = raw[key]

    process_section_deleted_files(raw, owner["email"])

    encrypted = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        actor=actor,
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 21 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section21(request: Request,
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

    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section, viewer_role=decoded.get("role"))


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section21(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one({"_id": ObjectId(decoded["sub"])})

    await SectionRepository.delete(str(user["_id"]), SECTION_ID)

    return {"message": "Section 21 deleted"}


# E2EE / older FE slug (finalwishes)
alias_router = APIRouter(
    prefix="/sections/section21-estate-planning-finalwishes",
    tags=["Section 21 – Estate Planning & Final Wishes"],
)
alias_router.add_api_route("", save_section21, methods=["POST"])
alias_router.add_api_route("", get_section21, methods=["GET"])
alias_router.add_api_route("", delete_section21, methods=["DELETE"])
