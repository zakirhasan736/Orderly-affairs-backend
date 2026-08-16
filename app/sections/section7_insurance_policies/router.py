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
from app.sections.section7_insurance_policies.schemas import (
    Section7InsurancePoliciesPayload,
)

router = APIRouter(
    prefix="/sections/section7-insurance-policies",
    tags=["Section 7 – Insurance Policies"],
)

SECTION_ID = "7"
SECTION_KEY = "section7_insurance_policies"
SUBSECTIONS = ["7A"]


# ---------------- SAVE / UPDATE ----------------

@router.post("")
async def save_section7(
    payload: Section7InsurancePoliciesPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    raw_data = payload.root

    # 🔥 DELETE REMOVED CLOUDINARY FILES

    # ✅ NORMALIZE DATA
    data = {
        subsection: [
            policy.model_dump(
                exclude_none=True,
                by_alias=True,
            )
            for policy in policies
        ]
        for subsection, policies in raw_data.items()
    }

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

    return {"message": "Section 7 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section7(request: Request,
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
        return {"data": {}}

    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section, viewer_role=decoded.get("role"))


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section7(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 7 deleted"}
