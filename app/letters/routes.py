# app/nok_letter/routes.py
from fastapi import APIRouter, Header, HTTPException, Query
from typing import Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from app.database import db, kits_collection
from .core import require_owner
from .models import NOKLetterIn, NOKLetterOut

router = APIRouter(prefix="/nok-letter", tags=["nok-letter"])

nok_letters_collection = db["nok_letters"]
users_collection = db["users"]


def to_out(doc: Dict[str, Any]) -> NOKLetterOut:
    # You can add nok_user_id to your Pydantic model if you want to expose it.
    # If your model doesn't have it, leaving it out is fine.
    return NOKLetterOut(
        id=str(doc["_id"]),
        owner_id=doc.get("owner_id"),
        letter_date=doc.get("letter_date"),
        letter_to=doc.get("letter_to"),
        letter_greeting=doc.get("letter_greeting"),
        letter_opening=doc.get("letter_opening"),
        kit_description=doc.get("kit_description"),
        access_url=doc.get("access_url"),
        login_credentials_text=doc.get("login_credentials_text"),
        nok_email=doc.get("nok_email"),
        nok_phone=doc.get("nok_phone"),
        password_card_location=doc.get("password_card_location"),
        accessible_sections=doc.get("accessible_sections"),
        key_bag_info=doc.get("key_bag_info"),
        key_bag_location=doc.get("key_bag_location"),
        documents_bag_info=doc.get("documents_bag_info"),
        documents_bag_location=doc.get("documents_bag_location"),
        incomplete_kit_message=doc.get("incomplete_kit_message"),
        closing_message=doc.get("closing_message"),
        letter_signature=doc.get("letter_signature"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


async def fetch_nok_by_id(owner_id: str, nok_id: str) -> Optional[Dict[str, Any]]:
    try:
        _id = ObjectId(nok_id)
    except Exception:
        return None
    doc = await users_collection.find_one({
        "_id": _id,
        "role": "nextkin",
        "owner_id": owner_id,
        "verified": True
    })
    if not doc:
        return None
    return {
        "full_name": doc.get("full_name"),
        "email": doc.get("email"),
        "phone_number": doc.get("phone_number"),
        "card_storage_location": doc.get("card_storage_location"),
        "access_level": doc.get("access_level"),
        "authorized_sections": [str(s) for s in (doc.get("authorized_sections") or [])],
        "_id": str(doc.get("_id")),
    }


async def fetch_primary_nok(owner_id: str) -> Optional[Dict[str, Any]]:
    cursor = users_collection.find(
        {"owner_id": owner_id, "role": "nextkin", "verified": True}
    )
    people = await cursor.to_list(length=None)
    if not people:
        return None

    def score(p: Dict[str, Any]):
        lvl = (p.get("access_level") or "").lower()
        is_full = 2 if ("full kit access" in lvl or "full access" in lvl) else 0
        has_sections = 1 if (p.get("authorized_sections") or []) else 0
        immediate = 1 if p.get("immediate_access") else 0
        created_at = p.get("created_at")
        ts = created_at.timestamp() if isinstance(created_at, datetime) else 0
        return (is_full, has_sections, immediate, ts)

    p = max(people, key=score)
    return {
        "full_name": p.get("full_name"),
        "email": p.get("email"),
        "phone_number": p.get("phone_number"),
        "card_storage_location": p.get("card_storage_location"),
        "access_level": p.get("access_level"),
        "authorized_sections": [str(s) for s in (p.get("authorized_sections") or [])],
        "_id": str(p.get("_id")),
    }


async def build_section_catalog(owner_id: str) -> Dict[str, str]:
    kit = await kits_collection.find_one({"owner_id": owner_id})
    catalog: Dict[str, str] = {}
    if not kit:
        return catalog
    for s in kit.get("sections", []):
        sid = str(s.get("id"))
        title = (s.get("title") or "").strip()
        if sid and title:
            catalog[sid] = title
        for ss in s.get("subsections") or []:
            ssid = str(ss.get("id"))
            stitle = (ss.get("title") or "").strip()
            if ssid and stitle:
                catalog[ssid] = stitle
    return catalog


def build_accessible_sections_text(nok: Optional[Dict[str, Any]],
                                   catalog: Optional[Dict[str, str]] = None) -> Optional[str]:
    if not nok:
        return None
    level = (nok.get("access_level") or "").lower()
    sections = [str(x) for x in (nok.get("authorized_sections") or [])]

    if "full kit access" in level or "full access" in level:
        return (
            "Once you log in, you'll be able to manage all sections of the kit on my behalf:\n\n"
            "• All sections (Full Kit Access)"
        )

    if sections:
        catalog = catalog or {}
        bullets = []
        for sid in sections:
            label = catalog.get(sid)
            bullets.append(f"• {sid}" + (f" — {label}" if label else ""))
        return (
            "Once you log in, you'll be able to manage the sections below on my behalf:\n\n"
            f"Selected Sections ({len(sections)}):\n" + "\n".join(bullets)
        )

    return (
        "Once you log in, you'll be able to manage the sections below on my behalf:\n\n"
        "• (No sections configured)"
    )


async def apply_autofill(owner_id: str, payload: NOKLetterIn, nok_id: Optional[str]) -> Dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)

    # pick the NOK: explicit id -> primary fallback
    nok = None
    if nok_id:
        nok = await fetch_nok_by_id(owner_id, nok_id)
        if not nok:
            # if invalid id given, keep letter usable by falling back
            nok = await fetch_primary_nok(owner_id)
    else:
        nok = await fetch_primary_nok(owner_id)

    if nok:
        if not data.get("letter_to"):
            data["letter_to"] = nok.get("full_name")

        if not data.get("nok_email"):
            data["nok_email"] = nok.get("email")

        if not data.get("nok_phone"):
            data["nok_phone"] = nok.get("phone_number")

        if not data.get("password_card_location"):
            data["password_card_location"] = nok.get("card_storage_location")


        if not data.get("accessible_sections"):
            catalog = await build_section_catalog(owner_id)
            text = build_accessible_sections_text(nok, catalog)
            if text:
                data["accessible_sections"] = text

        # store which NOK this letter was generated for (so you can have one per NOK)
        data.setdefault("nok_user_id", nok.get("_id"))

    data.setdefault("letter_greeting", "Dear")
    data.setdefault("access_url", "https://orderly-affairs.com")
    return data


@router.get("", response_model=NOKLetterOut)
async def get_my_nok_letter(
    authorization: str = Header(None),
    nok_id: Optional[str] = Query(None, description="Target NOK user _id")
):
    owner = await require_owner(authorization)
    owner_id = str(owner["_id"])

    # Try to find a doc for (owner, nok) first; fall back to legacy one-per-owner
    match: Dict[str, Any] = {"owner_id": owner_id}
    if nok_id:
        match["nok_user_id"] = nok_id

    doc = await nok_letters_collection.find_one(match)
    if doc:
        return to_out(doc)

    # No doc yet -> build and insert
    merged = await apply_autofill(owner_id, NOKLetterIn(), nok_id)
    now = datetime.utcnow()
    new_doc = {**merged, "owner_id": owner_id, "created_at": now, "updated_at": now}
    res = await nok_letters_collection.insert_one(new_doc)
    new_doc["_id"] = res.inserted_id
    return to_out(new_doc)


@router.post("", response_model=NOKLetterOut)
async def create_or_replace_my_nok_letter(
    payload: NOKLetterIn,
    authorization: str = Header(None),
    nok_id: Optional[str] = Query(None)
):
    owner = await require_owner(authorization)
    owner_id = str(owner["_id"])
    merged = await apply_autofill(owner_id, payload, nok_id)

    # Upsert by (owner_id, nok_user_id?) to allow one letter per NOK
    match: Dict[str, Any] = {"owner_id": owner_id}
    if merged.get("nok_user_id"):
        match["nok_user_id"] = merged["nok_user_id"]

    now = datetime.utcnow()
    existing = await nok_letters_collection.find_one(match)
    if existing:
        await nok_letters_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {**merged, "updated_at": now}},
        )
        doc = await nok_letters_collection.find_one({"_id": existing["_id"]})
        return to_out(doc)

    doc = {**merged, "owner_id": owner_id, "created_at": now, "updated_at": now}
    res = await nok_letters_collection.insert_one(doc)
    doc["_id"] = res.inserted_id
    return to_out(doc)


@router.put("", response_model=NOKLetterOut)
async def update_my_nok_letter(
    payload: NOKLetterIn,
    authorization: str = Header(None),
    nok_id: Optional[str] = Query(None)
):
    owner = await require_owner(authorization)
    owner_id = str(owner["_id"])

    # Compute merge (ensures text matches the chosen NOK if any)
    merged = await apply_autofill(owner_id, payload, nok_id)

    match: Dict[str, Any] = {"owner_id": owner_id}
    if merged.get("nok_user_id"):
        match["nok_user_id"] = merged["nok_user_id"]

    existing = await nok_letters_collection.find_one(match)
    if not existing:
        raise HTTPException(status_code=404, detail="NOK letter not found")

    await nok_letters_collection.update_one(
        {"_id": existing["_id"]},
        {"$set": {**merged, "updated_at": datetime.utcnow()}},
    )
    doc = await nok_letters_collection.find_one({"_id": existing["_id"]})
    return to_out(doc)
