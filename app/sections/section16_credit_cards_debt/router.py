from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from app.security.access_control import assert_section_read_access
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.crypto import encrypt_data, decrypt_data
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import delete_file

from .schemas import Section16CreditCardsDebtPayload

router = APIRouter(
    prefix="/sections/section16-credit-cards-debt",
    tags=["Section 16 – Credit Cards & Debt"],
)

SECTION_ID = "16"
SECTION_KEY = "section16_credit_cards_debt"
SUBSECTIONS = ["16A", "16B"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section16(
    payload: Section16CreditCardsDebtPayload,
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

    # 🔄 Normalize data
    data = {}

    if "16A" in raw_data and isinstance(raw_data["16A"], list):
        data["16A"] = raw_data["16A"]

    if "16B" in raw_data and isinstance(raw_data["16B"], list):
        data["16B"] = raw_data["16B"]

    # 🔥 Cloudinary cleanup
    def cleanup_files(obj):
        if isinstance(obj, dict):
            if "_deleted_files" in obj:
                for public_id in obj["_deleted_files"]:
                    delete_file(public_id)
            for v in obj.values():
                cleanup_files(v)
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

    return {"message": "Section 16 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section16(authorization: str = Header(...)):
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
async def delete_section16(authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 16 deleted"}
