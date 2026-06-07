from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file
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
    authorization: str = Header(...),
):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner",
    })

    raw_data = payload.root

    # 🔥 DELETE REMOVED CLOUDINARY FILES
    for policies in raw_data.values():
        for policy in policies:
            for field in policy.model_dump().values():
                if isinstance(field, dict):
                    for public_id in field.get("_deleted_files", []):
                        delete_file(public_id)

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

    encrypted_payload = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 7 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section7(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
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

    decrypted = decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"])

    return {
        "section_id": SECTION_ID,
        "section_key": SECTION_KEY,
        "data": decrypted,
    }


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section7(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 7 deleted"}
