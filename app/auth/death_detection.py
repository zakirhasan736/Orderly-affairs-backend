from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.auth.service import mark_owner_deceased
from app.database import kits_collection, users_collection

# Checklist item IDs that imply the trusted Next-of-Kin is handling a passing.
DEATH_SIGNAL_CHECKLIST_ITEMS = frozenset(
    {
        "gather_documents",
        "notify_immediate",
        "notify_banks",
        "notify_employer",
        "contact_insurance",
        "funeral_arrangements",
        "locate_will",
        "freeze_accounts",
    }
)

OWNER_INACTIVE_DAYS = 90
OWNER_FOLLOWUP_DAYS = 15
MIN_DEATH_SIGNAL_CHECKS = 2


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _owner_last_activity(owner: dict) -> datetime | None:
    return owner.get("last_login_at") or owner.get("created_at")


async def record_owner_last_login(owner_email: str) -> None:
    await users_collection.update_one(
        {"email": owner_email.lower(), "role": "owner"},
        {
            "$set": {"last_login_at": datetime.utcnow()},
            "$unset": {"inactivity_warning_sent_at": ""},
        },
    )


async def record_nextkin_last_login(nextkin_id: str) -> None:
    try:
        nk_object_id = ObjectId(nextkin_id)
    except Exception:
        return

    await users_collection.update_one(
        {"_id": nk_object_id, "role": "nextkin"},
        {"$set": {"last_login_at": datetime.utcnow()}},
    )


async def owner_inactive_long_enough(owner: dict) -> bool:
    reference = _as_utc(_owner_last_activity(owner))
    if not reference:
        return True

    threshold = datetime.now(timezone.utc) - timedelta(days=OWNER_INACTIVE_DAYS)
    return reference <= threshold


async def count_death_signals_for_nextkin(
    owner_id: str,
    nextkin_id: str,
    latest_items: dict | None = None,
) -> int:
    checked: set[str] = set()

    cursor = kits_collection.find(
        {
            "owner_id": owner_id,
            "nextkin_id": nextkin_id,
        }
    )

    async for doc in cursor:
        items = doc.get("items") or {}
        for item_id in DEATH_SIGNAL_CHECKLIST_ITEMS:
            if items.get(item_id):
                checked.add(item_id)

    if latest_items:
        for item_id in DEATH_SIGNAL_CHECKLIST_ITEMS:
            if latest_items.get(item_id):
                checked.add(item_id)

    return len(checked)


async def maybe_detect_owner_deceased_from_checklist(
    *,
    owner_id: str,
    nextkin_id: str,
    items: dict,
) -> dict | None:
    try:
        owner_object_id = ObjectId(owner_id)
        nextkin_object_id = ObjectId(nextkin_id)
    except Exception:
        return None

    owner = await users_collection.find_one(
        {"_id": owner_object_id, "role": "owner"}
    )
    if not owner or owner.get("owner_status") == "deceased":
        return None

    nextkin = await users_collection.find_one(
        {"_id": nextkin_object_id, "role": "nextkin"}
    )
    if not nextkin or not nextkin.get("immediate_access"):
        return None

    if nextkin.get("owner_id") != owner_id:
        return None

    death_signal_count = await count_death_signals_for_nextkin(
        owner_id,
        nextkin_id,
        items,
    )
    if death_signal_count < MIN_DEATH_SIGNAL_CHECKS:
        return None

    return await mark_owner_deceased(
        owner_id=owner_id,
        reported_by_nextkin_id=nextkin_id,
        source="checklist_death_signal",
    )
