from app.security.token_resolver import decode_access_token
from app.security.cookie_auth import NOK_ACCESS_COOKIE
from fastapi import APIRouter, Header, HTTPException, Request
from bson import ObjectId
from typing import Any, Dict, List
from datetime import datetime
from app.database import db, kits_collection, users_collection, section_data_collection, messageofnextkin_collection
from app.security.checklist_crypto import load_checklist_items, prepare_checklist_for_storage
from app.security.kit_data_crypto import (
    prepare_kit_section_for_storage,
    prepare_kit_subsection_for_storage,
)
from app.security.message_crypto import load_message
from app.security.nok_letter_crypto import load_nok_letter
from app.security.section_crypto import decrypt_section_data
from app.security.section_e2ee import present_kit_section
from app.auth.death_detection import maybe_detect_owner_deceased_from_checklist

from .models import ChecklistUpdate, SectionInput, SubsectionInput, TogglesInput
from .core import require_owner, require_nok, get_or_init_kit, ensure_section_struct, ensure_subsection_struct, filter_sections_for_nok
from app.notifications.personal_message_emails import send_personal_message_email

router = APIRouter(prefix="/kit", tags=["kit-core"])

# 1) OWNER — get full kit
@router.get("")
async def get_kit(request: Request, authorization: str | None = Header(default=None)):
    owner = await require_owner(request, authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    return kit

# 2) NOK — get filtered kit (respect access list/full)
@router.get("/for-nok")
async def get_kit_for_nok(request: Request, authorization: str | None = Header(default=None)):
    nk, ctx = await require_nok(request, authorization)
    kit = await get_or_init_kit(ctx["owner_id"])
    return filter_sections_for_nok(kit, nk)

# 3) OWNER — upsert a whole section data (e.g., "12")
@router.put("/section/{section_id}")
async def upsert_section(section_id: str, payload: SectionInput, request: Request, authorization: str | None = Header(default=None)):
    owner = await require_owner(request, authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    ensure_section_struct(kit, section_id)
    owner_id = str(owner["_id"])
    encrypted = prepare_kit_section_for_storage(owner_id, section_id, payload.data)
    res = await kits_collection.update_one(
        {"owner_id": owner_id},
        {
            "$set": {
                "sections.$[s].encrypted_data": encrypted["encrypted_data"],
                "sections.$[s].encryption_version": encrypted["encryption_version"],
                "updated_at": datetime.utcnow(),
            },
            "$unset": {"sections.$[s].data": ""},
        },
        array_filters=[{"s.id": section_id}],
        upsert=True,
    )
    return {"message": "Section upserted", "modified": getattr(res, "modified_count", 0)}

# 4) OWNER — upsert a subsection (e.g., section "3", subsection "3A")
@router.put("/section/{section_id}/subsection/{sub_id}")
async def upsert_subsection(section_id: str, sub_id: str, payload: SubsectionInput, request: Request, authorization: str | None = Header(default=None)):
    owner = await require_owner(request, authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    ensure_subsection_struct(kit, section_id, sub_id)
    owner_id = str(owner["_id"])
    encrypted = prepare_kit_subsection_for_storage(owner_id, sub_id, payload.data)
    res = await kits_collection.update_one(
        {"owner_id": owner_id},
        {
            "$set": {
                "sections.$[s].subsections.$[ss].encrypted_data": encrypted["encrypted_data"],
                "sections.$[s].subsections.$[ss].encryption_version": encrypted["encryption_version"],
                "sections.$[s].subsections.$[ss].updated_at": datetime.utcnow(),
            },
            "$unset": {"sections.$[s].subsections.$[ss].data": ""},
            "$inc": {"sections.$[s].subsections.$[ss].version": 1},
        },
        array_filters=[{"s.id": section_id}, {"ss.id": sub_id}],
        upsert=True,
    )
    return {"message": "Subsection upserted", "modified": getattr(res, "modified_count", 0)}

# 5) OWNER — toggles (disabled sections/subsections)
@router.put("/toggles")
async def update_toggles(payload: TogglesInput, request: Request, authorization: str | None = Header(default=None)):
    owner = await require_owner(request, authorization)
    res = await kits_collection.update_one(
        {"owner_id": str(owner["_id"])},
        {
            "$set": {
                "disabled_sections": payload.disabled_sections,
                "disabled_subsections": payload.disabled_subsections,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    return {"message": "Toggles updated", "modified": getattr(res, "modified_count", 0)}

# 6) OWNER — one-off migration from old formData shape
@router.post("/migrate-from-forms")
async def migrate_from_forms(payload: Dict[str, Any], request: Request, authorization: str | None = Header(default=None)):
    """
    payload example (from your localStorage dump):
    {
      "formData": { "4": {...}, "4A": {...}, "12": {...}, "12A": {...} },
      "disabledSections": {...},
      "disabledSubsections": {...}
    }
    """
    owner = await require_owner(request, authorization)
    owner_id = str(owner["_id"])
    old = payload or {}
    form = old.get("formData") or {}

    kit = await get_or_init_kit(owner_id)
    sections_index = {s["id"]: s for s in kit["sections"]}

    def ensure_section(sec_id: str) -> Dict[str, Any]:
        if sec_id not in sections_index:
            obj = {"id": sec_id, "title": "", "data": {}, "subsections": []}
            sections_index[sec_id] = obj
            kit["sections"].append(obj)
        return sections_index[sec_id]

    # rebuild from flat keys
    for key, value in form.items():
        if key.isdigit():
            ensure_section(key)["data"] = value
        else:
            sec_id = "".join([c for c in key if c.isdigit()]) or key
            sub_id = key
            sec = ensure_section(sec_id)
            # ensure unique
            if not any(ss.get("id") == sub_id for ss in sec["subsections"]):
                sec["subsections"].append({"id": sub_id, "title": "", "data": value, "version": 1})
            else:
                for ss in sec["subsections"]:
                    if ss["id"] == sub_id:
                        ss["data"] = value
                        break

    # toggles
    kit["disabled_sections"] = old.get("disabledSections", kit.get("disabled_sections", {}))
    kit["disabled_subsections"] = old.get("disabledSubsections", kit.get("disabled_subsections", {}))
    kit["updated_at"] = datetime.utcnow()

    for section in kit.get("sections", []):
        section_id = str(section.get("id") or "")
        if section.get("data") is not None and not section.get("encrypted_data"):
            encrypted = prepare_kit_section_for_storage(owner_id, section_id, section["data"])
            section.update(encrypted)
            section.pop("data", None)
        for subsection in section.get("subsections", []):
            sub_id = str(subsection.get("id") or "")
            if subsection.get("data") is not None and not subsection.get("encrypted_data"):
                encrypted = prepare_kit_subsection_for_storage(owner_id, sub_id, subsection["data"])
                subsection.update(encrypted)
                subsection.pop("data", None)

    await kits_collection.replace_one({"owner_id": owner_id}, kit, upsert=True)
    return {"message": "Migration completed", "sections": len(kit["sections"])}

@router.get("/nok")
async def get_kit_for_nextkin(request: Request, authorization: str | None = Header(default=None)):
    decoded = decode_access_token(request, authorization, access_cookie=NOK_ACCESS_COOKIE)

    if not decoded or decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only Next-of-Kin allowed")

    # -------------------------
    # 1️⃣ Load Next-of-Kin
    # -------------------------
    try:
        nextkin_id = ObjectId(decoded["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    nextkin = await users_collection.find_one({
        "_id": nextkin_id,
        "role": "nextkin",
    })

    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    if not nextkin.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    owner_id = nextkin["owner_id"]

    # -------------------------
    # 2️⃣ Load Kit Sections
    # -------------------------
    full_access = nextkin.get("access_level") == "Full Kit Access"
    allowed_sections = set(nextkin.get("authorized_sections", []))

    sections_cursor = section_data_collection.find({
        "owner_id": owner_id
    })

    sections = []
    async for section in sections_cursor:
        section_id = section.get("section_id")

        if not full_access and section_id not in allowed_sections:
            continue

        sections.append(present_kit_section(owner_id, section))

    # -------------------------
    # 3️⃣ Load NOK Letter
    # -------------------------
    nok_letters_collection = db["nok_letters"]
    nok_letter = await nok_letters_collection.find_one({
        "owner_id": owner_id,
        "nok_user_id": str(nextkin["_id"]),
    })

    if nok_letter:
        nok_letter = load_nok_letter(nok_letter)
        nok_letter["_id"] = str(nok_letter["_id"])

    # -------------------------
    # 4️⃣ Load Personal Messages (NO CONTENT)
    # -------------------------
    messages_cursor = messageofnextkin_collection.find({
        "owner_id": owner_id,
        "is_deleted": False,
    })

    messages = []
    async for msg in messages_cursor:
        messages.append({
            "id": str(msg["_id"]),
            "recipient": msg.get("recipient"),
            "recipient_email": msg.get("recipient_email"),
            "message_type": msg.get("message_type"),
            "delivery_trigger": msg.get("delivery_trigger"),
            "delivery_date": msg.get("delivery_date"),
            "delivery_occasion": msg.get("delivery_occasion"),
            "status": msg.get("status"),
            "created_at": msg.get("created_at"),
            "sent_at": msg.get("sent_at"),
        })

    checklists_cursor = kits_collection.find({
    "owner_id": owner_id,
    "nextkin_id": str(nextkin["_id"]),
    })

    checklists = {}
    async for c in checklists_cursor:
        checklists[c["section_id"]] = load_checklist_items(c)

    # -------------------------
    # 5️⃣ Final Response
    # -------------------------
    return {
        "nextkin": {
            "id": str(nextkin["_id"]),
            "email": nextkin.get("email"),
            "full_name": nextkin.get("full_name"),
            "relationship": nextkin.get("relationship"),
            "access_level": nextkin.get("access_level"),
        },
    "owner_id": owner_id,
    "sections": sections,
    "nok_letter": nok_letter,
    "messages": messages,
    "checklists": checklists,
    }

@router.post("/deliver/{message_id}")
async def deliver_message(
    message_id: str,
    request: Request, authorization: str | None = Header(default=None)
):
    decoded = decode_access_token(request, authorization, access_cookie=NOK_ACCESS_COOKIE)

    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only NOK can deliver messages")

    owner_id = decoded.get("owner_id")
    if not owner_id:
        raise HTTPException(status_code=400, detail="Owner ID missing")

    # 1️⃣ Load message
    msg = await messageofnextkin_collection.find_one({
        "_id": ObjectId(message_id),
        "owner_id": owner_id,
        "status": "pending",
        "is_deleted": False,
    })

    if not msg:
        raise HTTPException(
            status_code=404,
            detail="Message not found, already sent, or unauthorized"
        )

    if msg.get("delivery_trigger") == "death":
        owner = await users_collection.find_one(
            {"_id": ObjectId(owner_id), "role": "owner"}
        )
        if not owner or owner.get("owner_status") != "deceased":
            raise HTTPException(
                status_code=403,
                detail="This message can only be delivered after the owner has been marked deceased",
            )

    # 2️⃣ Decrypt payload
    msg = load_message(msg)

    # 3️⃣ Send email immediately (IGNORES date/death trigger)
    await send_personal_message_email(letter=msg)

    # 4️⃣ Mark as sent
    await messageofnextkin_collection.update_one(
        {"_id": msg["_id"]},
        {"$set": {
            "status": "sent",
            "sent_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }}
    )

    return {"status": "delivered"}

@router.post("/checklist")
async def save_checklist_progress(
    payload: ChecklistUpdate,
    request: Request, authorization: str | None = Header(default=None)
):
    decoded = decode_access_token(request, authorization, access_cookie=NOK_ACCESS_COOKIE)

    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only NOK allowed")

    nextkin_id = decoded["sub"]
    owner_id = decoded.get("owner_id")

    if not owner_id:
        raise HTTPException(status_code=400, detail="Owner ID missing")

    encrypted_checklist = prepare_checklist_for_storage(
        owner_id=owner_id,
        nextkin_id=nextkin_id,
        section_id=payload.section_id,
        items=payload.items,
    )
    await kits_collection.update_one(
        {
            "owner_id": owner_id,
            "nextkin_id": nextkin_id,
            "section_id": payload.section_id,
        },
        {
            "$set": {
                **encrypted_checklist,
                "owner_id": owner_id,
                "nextkin_id": nextkin_id,
                "section_id": payload.section_id,
                "updated_at": datetime.utcnow(),
            },
            "$unset": {"items": ""},
        },
        upsert=True,
    )

    detection = await maybe_detect_owner_deceased_from_checklist(
        owner_id=owner_id,
        nextkin_id=nextkin_id,
        items=payload.items,
    )

    return {
        "status": "saved",
        "death_signals_ready": bool(detection and detection.get("death_signals_ready")),
        "death_signal_count": detection.get("death_signal_count") if detection else 0,
        "owner_status": detection.get("owner_status") if detection else None,
    }