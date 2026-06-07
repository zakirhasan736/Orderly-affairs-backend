from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file

from .schemas import Section18EmploymentBusinessPayload

router = APIRouter(
    prefix="/sections/section18-employment-business",
    tags=["Section 18 – Employment & Business"],
)

SECTION_ID = "18"
SECTION_KEY = "section18_employment_business"
SUBSECTIONS = ["18A", "18B", "18C", "18D"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section18(
    payload: Section18EmploymentBusinessPayload,
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

    # Normalize subsections
    if isinstance(raw_data.get("18A"), dict):
        data["18A"] = raw_data["18A"]

    for key in ["18B", "18C", "18D"]:
        if isinstance(raw_data.get(key), list):
            data[key] = raw_data[key]

    # 🔥 Cloudinary cleanup
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

    encrypted_payload = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 18 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section18(authorization: str = Header(...)):
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

    decrypted = decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"])

    return {
        "section_key": SECTION_KEY,
        "data": decrypted,
    }


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section18(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 18 deleted"}
