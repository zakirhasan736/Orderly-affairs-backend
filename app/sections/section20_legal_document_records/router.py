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

from .schemas import Section20LegalDocumentsPayload

# Matches production / committed FastAPI slug (plural "documents").
router = APIRouter(
    prefix="/sections/section20-legal-documents-records",
    tags=["Section 20 – Legal Documents & Records"],
)

SECTION_ID = "20"
SECTION_KEY = "section20_legal_documents_records"
SUBSECTIONS = ["20A", "20B", "20C"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section20(
    payload: Section20LegalDocumentsPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    raw_data = payload.root
    data = {}

    if isinstance(raw_data.get("20A"), dict):
        data["20A"] = raw_data["20A"]

    if isinstance(raw_data.get("20B"), dict):
        data["20B"] = raw_data["20B"]

    if isinstance(raw_data.get("20C"), list):
        data["20C"] = raw_data["20C"]

    process_section_deleted_files(raw_data, owner["email"])

    encrypted = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        actor=actor,
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 20 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section20(request: Request,
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
async def delete_section20(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one({"_id": ObjectId(decoded["sub"])})

    await SectionRepository.delete(str(user["_id"]), SECTION_ID)

    return {"message": "Section 20 deleted"}


# E2EE / older FE slug (singular "document")
alias_router = APIRouter(
    prefix="/sections/section20-legal-document-records",
    tags=["Section 20 – Legal Documents & Records"],
)
alias_router.add_api_route("", save_section20, methods=["POST"])
alias_router.add_api_route("", get_section20, methods=["GET"])
alias_router.add_api_route("", delete_section20, methods=["DELETE"])
