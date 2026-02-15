from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.crypto import encrypt_data, decrypt_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file

from .schemas import Section19AssetsValuablesPayload

router = APIRouter(
    prefix="/sections/section19-assets-valuables",
    tags=["Section 19 – Assets & Valuables"],
)

SECTION_ID = "19"
SECTION_KEY = "section19_assets_valuables"
SUBSECTIONS = ["19A", "19B"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section19(
    payload: Section19AssetsValuablesPayload,
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
    data = {}

    # ---------- Normalize subsections ----------
    for key in ["19A", "19B"]:
        if isinstance(raw_data.get(key), list):
            data[key] = raw_data[key]

    # ---------- Cloudinary cleanup ----------
    def cleanup_files(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                cleanup_files(v)
            if "_deleted_files" in obj:
                for public_id in obj["_deleted_files"]:
                    delete_file(public_id)
        elif isinstance(obj, list):
            for i in obj:
                cleanup_files(i)

    cleanup_files(raw_data)

    encrypted_payload = encrypt_data(data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 19 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section19(authorization: str = Header(...)):
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
async def delete_section19(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 19 deleted"}
