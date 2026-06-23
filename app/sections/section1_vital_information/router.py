from datetime import datetime

from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId

from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.sections.section1_vital_information.schemas import Section1VitalInformationPayload
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.security.jwt_handler import verify_token
from app.security.access_control import assert_section_read_access

router = APIRouter(
    prefix="/sections/section1-vital-information",
    tags=["Section 1 – Vital Information"],
)

SECTION_ID = "1"
SECTION_KEY = "section1_vitalinformation"
SUBSECTIONS = ["1A", "1C"]


@router.post("")
async def save_section1(
    payload: Section1VitalInformationPayload,
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

    encrypted = encrypt_section_data(str(owner["_id"]), SECTION_ID, payload.dict())

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted,
        subsections=SUBSECTIONS,
    )

    legal_name = (payload.vital_info or {}).get("full_legal_name")
    if isinstance(legal_name, str) and legal_name.strip():
        await users_collection.update_one(
            {"_id": owner["_id"]},
            {
                "$set": {
                    "full_name": legal_name.strip(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    return {"message": "Section 1 saved"}


@router.get("")
async def get_section1(authorization: str = Header(...)):
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


@router.delete("")
async def delete_section1(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner",
    })

    await SectionRepository.delete(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
    )

    return {"message": "Section 1 deleted"}
