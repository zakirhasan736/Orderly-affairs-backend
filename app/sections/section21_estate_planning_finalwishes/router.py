from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file

from .schemas import Section21EstatePlanningPayload

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

    raw = payload.root
    data = {}

    for key in ["21A", "21B", "21C"]:
        if isinstance(raw.get(key), dict):
            data[key] = raw[key]

    # 🔥 Cloudinary cleanup
    def cleanup(obj):
        if isinstance(obj, dict):
            if "_deleted_files" in obj:
                for pid in obj["_deleted_files"]:
                    delete_file(pid)
            for v in obj.values():
                cleanup(v)
        elif isinstance(obj, list):
            for i in obj:
                cleanup(i)

    cleanup(raw)

    encrypted = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 21 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section21(authorization: str = Header(...)):
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

    return {
        "section_key": SECTION_KEY,
        "data": decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"]),
    }


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section21(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one({"_id": ObjectId(decoded["sub"])})

    await SectionRepository.delete(str(user["_id"]), SECTION_ID)

    return {"message": "Section 21 deleted"}
