from fastapi import APIRouter, Header, HTTPException
from bson import ObjectId
from typing import Any, Dict, List
from datetime import datetime
from app.database import kits_collection, users_collection, section_data_collection, messageofnextkin_collection, letters_collection
from app.security.jwt_handler import verify_token
from app.security.crypto import decrypt_data
from .models import ChecklistUpdate, SectionInput, SubsectionInput, TogglesInput
from .core import require_owner, require_nok, get_or_init_kit, ensure_section_struct, ensure_subsection_struct, filter_sections_for_nok
from app.notifications.nextkin_emails import send_message_email

router = APIRouter(prefix="/kit", tags=["kit-core"])

# 1) OWNER — get full kit
@router.get("")
async def get_kit(authorization: str = Header(None)):
    owner = await require_owner(authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    return kit

# 2) NOK — get filtered kit (respect access list/full)
@router.get("/for-nok")
async def get_kit_for_nok(authorization: str = Header(None)):
    nk, ctx = await require_nok(authorization)
    kit = await get_or_init_kit(ctx["owner_id"])
    return filter_sections_for_nok(kit, nk)

# 3) OWNER — upsert a whole section data (e.g., "12")
@router.put("/section/{section_id}")
async def upsert_section(section_id: str, payload: SectionInput, authorization: str = Header(None)):
    owner = await require_owner(authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    ensure_section_struct(kit, section_id)
    res = await kits_collection.update_one(
        {"owner_id": str(owner["_id"])},
        {
            "$set": {
                "sections.$[s].data": payload.data,
                "updated_at": datetime.utcnow(),
            }
        },
        array_filters=[{"s.id": section_id}],
        upsert=True,
    )
    return {"message": "Section upserted", "modified": getattr(res, "modified_count", 0)}

# 4) OWNER — upsert a subsection (e.g., section "3", subsection "3A")
@router.put("/section/{section_id}/subsection/{sub_id}")
async def upsert_subsection(section_id: str, sub_id: str, payload: SubsectionInput, authorization: str = Header(None)):
    owner = await require_owner(authorization)
    kit = await get_or_init_kit(str(owner["_id"]))
    ensure_subsection_struct(kit, section_id, sub_id)
    res = await kits_collection.update_one(
        {"owner_id": str(owner["_id"])},
        {
            "$set": {
                "sections.$[s].subsections.$[ss].data": payload.data,
                "sections.$[s].subsections.$[ss].updated_at": datetime.utcnow(),
            },
            "$inc": {"sections.$[s].subsections.$[ss].version": 1},
        },
        array_filters=[{"s.id": section_id}, {"ss.id": sub_id}],
        upsert=True,
    )
    return {"message": "Subsection upserted", "modified": getattr(res, "modified_count", 0)}

# 5) OWNER — toggles (disabled sections/subsections)
@router.put("/toggles")
async def update_toggles(payload: TogglesInput, authorization: str = Header(None)):
    owner = await require_owner(authorization)
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
async def migrate_from_forms(payload: Dict[str, Any], authorization: str = Header(None)):
    """
    payload example (from your localStorage dump):
    {
      "formData": { "4": {...}, "4A": {...}, "12": {...}, "12A": {...} },
      "disabledSections": {...},
      "disabledSubsections": {...}
    }
    """
    owner = await require_owner(authorization)
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

    await kits_collection.replace_one({"owner_id": owner_id}, kit, upsert=True)
    return {"message": "Migration completed", "sections": len(kit["sections"])}

@router.get("/nok")
async def get_kit_for_nextkin(authorization: str = Header(None)):
    # -------------------------
    # 0️⃣ Auth
    # -------------------------
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)

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

        try:
            decrypted = decrypt_data(section.get("encrypted_data", ""))
        except Exception:
            decrypted = {}

        sections.append({
            "id": section_id,
            "key": section.get("section_key"),
            "data": decrypted,
            "subsections": section.get("subsections", []),
            "updated_at": section.get("updated_at"),
        })

    # -------------------------
    # 3️⃣ Load NOK Letter
    # -------------------------
    nok_letter = await letters_collection.find_one({
        "owner_id": owner_id,
        "nok_user_id": str(nextkin["_id"]),
    })

    if nok_letter:
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
        checklists[c["section_id"]] = c["items"]

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
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    user = verify_token(token)

    if user.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only NOK can deliver messages")

    owner_id = user.get("owner_id")
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

    # 2️⃣ Decrypt payload
    payload = decrypt_data(msg["encrypted_payload"])
    subject = payload.get("subject") or "A message from your loved one"
    content = payload.get("content") or ""

    # 3️⃣ Send email immediately (IGNORES date/death trigger)
    await send_message_email(
        to=msg["recipient_email"],
        subject=subject,
        html=f"""
        <div style="font-family:Arial,sans-serif;line-height:1.6">
          <p>{content}</p>
          <hr />
          <small>Delivered via Orderly Affairs</small>
        </div>
        """,
    )

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
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    user = verify_token(token)

    if user.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only NOK allowed")

    nextkin_id = user["sub"]
    owner_id = user.get("owner_id")

    if not owner_id:
        raise HTTPException(status_code=400, detail="Owner ID missing")

    await kits_collection.update_one(
        {
            "owner_id": owner_id,
            "nextkin_id": nextkin_id,
            "section_id": payload.section_id,
        },
        {
            "$set": {
                "items": payload.items,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )

    return {"status": "saved"}