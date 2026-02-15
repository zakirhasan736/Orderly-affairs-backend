# app/sections/section8_community_membership/router.py

from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.crypto import encrypt_data, decrypt_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file
from app.sections.section8_community_membership.schemas import (
    Section8CommunityMembershipPayload,
)

router = APIRouter(
    prefix="/sections/section8-community-membership",
    tags=["Section 8 – Community Membership"],
)

SECTION_ID = "8"
SECTION_KEY = "section8_community_membership"
SUBSECTIONS = ["8A"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section8(
    payload: Section8CommunityMembershipPayload,
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

    # ✅ Convert Pydantic → plain dict
    data = {
        subsection: [
            group.model_dump(exclude_none=True)
            for group in groups
        ]
        for subsection, groups in raw_data.items()
    }

    # 🔥 Delete removed Cloudinary files
    for groups in raw_data.values():
        for group in groups:
            for field in group.model_dump().values():
                if isinstance(field, dict):
                    for public_id in field.get("_deleted_files", []):
                        delete_file(public_id)

    # 🔐 Encrypt
    encrypted_payload = encrypt_data(data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 8 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section8(authorization: str = Header(...)):
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
        return {}

    decrypted = decrypt_data(section["encrypted_data"])

    return {
        "section_key": SECTION_KEY,
        "data": decrypted,
    }


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section8(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 8 deleted"}
