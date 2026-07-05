from fastapi import APIRouter, Header, HTTPException, Request
from bson import ObjectId

from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import encrypt_section_data, decrypt_section_data
from app.sections.section5_vehicles.schemas import Section5VehiclesPayload
from app.security.token_resolver import decode_owner_or_nok_token
from app.security.section_file_cleanup import process_section_deleted_files
from app.security.access_control import assert_section_read_access

router = APIRouter(
    prefix="/sections/section5-vehicles",
    tags=["Section 5 – Vehicles"],
)

SECTION_ID = "5"
SECTION_KEY = "section5_vehicles"


@router.post("")
async def save_section5(
    payload: Section5VehiclesPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner",
    })

    raw_data = payload.root

    # ✅ Convert Pydantic → dict
    data = {
        subsection: [
            vehicle.model_dump(exclude_none=True)
            for vehicle in vehicles
        ]
        for subsection, vehicles in raw_data.items()
    }

    # 🔥 DELETE REMOVED CLOUDINARY FILES

    # 🔐 ENCRYPT DATA
    process_section_deleted_files(data, owner['email'])

    encrypted_payload = encrypt_section_data(str(owner["_id"]), SECTION_ID, data)

    await SectionRepository.upsert(
        owner_id=str(owner["_id"]),
        section_id=SECTION_ID,
        section_key=SECTION_KEY,
        encrypted_data=encrypted_payload,  # 🔐 ENCRYPTED
        subsections=["5A"],
    )

    return {"message": "Section 5 saved successfully"}


@router.get("")
async def get_section5(request: Request,
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

    decrypted = decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"])

    return {
        "section_key": SECTION_KEY,
        "data": decrypted,
    }


@router.delete("")
async def delete_section5(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 5 deleted"}
