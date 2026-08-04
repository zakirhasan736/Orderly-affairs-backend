# app/sections/section12_banking_financial_accounts/router.py

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
from app.sections.section12_banking_financial_accounts.schemas import (
    Section12BankingFinancialAccountsPayload,
)

router = APIRouter(
    prefix="/sections/section12-banking-financial-accounts",
    tags=["Section 12 – Banking & Financial Accounts"],
)

SECTION_ID = "12"
SECTION_KEY = "section12_banking_financial_accounts"
SUBSECTIONS = ["12A", "12B"]


# ---------------- SAVE ----------------

@router.post("")
async def save_section12(
    payload: Section12BankingFinancialAccountsPayload,
    request: Request,
    authorization: str | None = Header(default=None),
):
    owner, actor = await require_section_write(
        request, authorization, SECTION_ID
    )
    if not owner:
        raise HTTPException(status_code=401)

    raw_data = payload.root

    # ✅ Convert Pydantic → dict
    data = {
        subsection: [
            item.model_dump(exclude_none=True)
            for item in items
        ]
        for subsection, items in raw_data.items()
    }

    # 🔥 Delete removed Cloudinary files

    # 🔐 Encrypt
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

    return {"message": "Section 12 saved successfully"}


# ---------------- GET ----------------

@router.get("")
async def get_section12(request: Request,
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

    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section)


# ---------------- DELETE ----------------

@router.delete("")
async def delete_section12(request: Request,
    authorization: str | None = Header(default=None)):
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one({"email": decoded["sub"]})

    await SectionRepository.delete(str(owner["_id"]), SECTION_ID)

    return {"message": "Section 12 deleted"}
