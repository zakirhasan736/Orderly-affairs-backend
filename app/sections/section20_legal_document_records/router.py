from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.crypto import encrypt_data, decrypt_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file

from .schemas import Section20LegalDocumentsPayload

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

    if isinstance(raw_data.get("20A"), dict):
        data["20A"] = raw_data["20A"]

    if isinstance(raw_data.get("20B"), dict):
        data["20B"] = raw_data["20B"]

    if isinstance(raw_data.get("20C"), list):
        data["20C"] = raw_data["20C"]

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

    cleanup(raw_data)

    encrypted = encrypt_data(data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    return {"message": "Section 20 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section20(authorization: str = Header(...)):
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
        "data": decrypt_data(section["encrypted_data"]),
    }


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section20(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one({"_id": ObjectId(decoded["sub"])})

    await SectionRepository.delete(str(user["_id"]), SECTION_ID)

    return {"message": "Section 20 deleted"}
