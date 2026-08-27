from datetime import datetime, timedelta, timezone

from bson import ObjectId

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


async def record_owner_last_login(owner_email: str) -> bool:
    """
    Stamp last login and increment login_count.
    Returns True when this owner has signed in before (returning user).
    """
    email = owner_email.lower()
    prior = await users_collection.find_one(
        {"email": email, "role": "owner"},
        {"login_count": 1, "last_login_at": 1},
    )
    returning = user_is_returning_login(prior)

    await users_collection.update_one(
        {"email": email, "role": "owner"},
        {
            "$set": {"last_login_at": datetime.utcnow()},
            "$inc": {"login_count": 1},
            "$unset": {"inactivity_warning_sent_at": ""},
        },
    )

    try:
        from app.auth.after_death_case import note_fresh_owner_login

        owner = await users_collection.find_one({"email": email, "role": "owner"})
        if owner:
            await note_fresh_owner_login(owner)
    except Exception as exc:
        print("⚠️ After-death login signal failed:", exc)

    return returning


def user_is_returning_login(user: dict | None) -> bool:
    """True when the user had at least one completed login before this attempt."""
    if not user:
        return False
    login_count = int(user.get("login_count") or 0)
    if login_count >= 1:
        return True
    return bool(user.get("last_login_at"))


def user_is_returning_for_session(user: dict | None) -> bool:
    """True for dashboard greeting after the first-ever login is complete."""
    if not user:
        return False
    login_count = int(user.get("login_count") or 0)
    if login_count >= 2:
        return True
    if login_count == 1:
        return False
    return bool(user.get("last_login_at"))


async def record_nextkin_last_login(nextkin_id: str) -> bool:
    try:
        nk_object_id = ObjectId(nextkin_id)
    except Exception:
        return False

    prior = await users_collection.find_one(
        {"_id": nk_object_id, "role": "nextkin"},
        {"login_count": 1, "last_login_at": 1},
    )
    returning = user_is_returning_login(prior)

    await users_collection.update_one(
        {"_id": nk_object_id, "role": "nextkin"},
        {
            "$set": {"last_login_at": datetime.utcnow()},
            "$inc": {"login_count": 1},
        },
    )
    return returning


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


async def evaluate_death_signals_from_checklist(
    *,
    owner_id: str,
    nextkin_id: str,
    items: dict,
) -> dict | None:
    """Return signal counts when thresholds are met — does NOT mark owner deceased."""
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

    now = datetime.utcnow()
    await users_collection.update_one(
        {"_id": owner_object_id},
        {
            "$set": {
                "death_signals_pending_confirmation": True,
                "death_signal_count": death_signal_count,
                "death_signals_reported_by": nextkin_id,
                "death_signals_updated_at": now,
                "updated_at": now,
            }
        },
    )

    return {
        "death_signals_ready": True,
        "death_signal_count": death_signal_count,
        "owner_status": owner.get("owner_status") or "alive",
    }


# Backwards-compatible alias — callers must not expect auto-deceased behavior.
async def maybe_detect_owner_deceased_from_checklist(
    *,
    owner_id: str,
    nextkin_id: str,
    items: dict,
) -> dict | None:
    return await evaluate_death_signals_from_checklist(
        owner_id=owner_id,
        nextkin_id=nextkin_id,
        items=items,
    )
