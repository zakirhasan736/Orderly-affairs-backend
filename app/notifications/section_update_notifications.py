from datetime import datetime, timedelta

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import nextkin_login_url, settings
from app.database import db, users_collection

# Sections that should NOT email immediate-access people when updated.
SECTIONS_EXCLUDED_FROM_UPDATE_NOTIFICATIONS = frozenset({"4", "8", "9", "11", "17"})

SECTION_TITLES: dict[str, str] = {
    "1": "Vital Information & Key Contacts",
    "2": "Access Management",
    "3": "Letter to Next of Kin",
    "4": "Personal Messages",
    "5": "Vehicles",
    "6": "Main Residence",
    "7": "Insurance Policies",
    "8": "Organizations & Memberships",
    "9": "Charitable Contributions",
    "10": "Education History",
    "11": "Military Service",
    "12": "Bank Accounts",
    "13": "Passwords & Online Accounts",
    "14": "Investments",
    "15": "Healthcare",
    "16": "Credit Cards & Debt",
    "17": "Family & Relationships",
    "18": "Employment & Income",
    "19": "Assets & Valuables",
    "20": "Legal Documents & Records",
    "21": "Estate Planning & Final Wishes",
}

NOTIFICATION_COOLDOWN = timedelta(minutes=30)
cooldown_collection = db["section_update_notification_cooldowns"]


def _has_full_kit_access(nextkin: dict) -> bool:
    level = (nextkin.get("access_level") or "").strip().lower()
    return level in {"full kit access", "full access", "full"}


def _section_matches_grant(section_id: str, granted: str) -> bool:
    if granted == section_id:
        return True

    if not granted.startswith(section_id):
        return False

    suffix = granted[len(section_id):]
    return bool(suffix) and suffix.isalpha()


def should_notify_nextkin_for_section(nextkin: dict, section_id: str) -> bool:
    if not nextkin.get("immediate_access", False):
        return False

    if section_id in SECTIONS_EXCLUDED_FROM_UPDATE_NOTIFICATIONS:
        return False

    if _has_full_kit_access(nextkin):
        return True

    allowed = {str(item) for item in (nextkin.get("authorized_sections") or [])}
    return any(_section_matches_grant(section_id, item) for item in allowed)


async def _cooldown_allows_send(owner_id: str, section_id: str) -> bool:
    now = datetime.utcnow()
    doc = await cooldown_collection.find_one(
        {"owner_id": owner_id, "section_id": section_id},
    )

    if doc and doc.get("last_sent_at"):
        elapsed = now - doc["last_sent_at"]
        if elapsed < NOTIFICATION_COOLDOWN:
            return False

    await cooldown_collection.update_one(
        {"owner_id": owner_id, "section_id": section_id},
        {"$set": {"last_sent_at": now, "updated_at": now}},
        upsert=True,
    )
    return True


async def _send_section_update_email(
    *,
    nextkin: dict,
    owner: dict,
    section_id: str,
) -> None:
    section_title = SECTION_TITLES.get(section_id, f"Section {section_id}")
    owner_name = owner.get("full_name") or owner.get("email") or "The kit owner"
    nk_name = nextkin.get("full_name") or nextkin.get("email")
    login_url = nextkin_login_url()

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
      <p>Hello {nk_name},</p>
      <p>
        <strong>{owner_name}</strong> has updated
        <strong>{section_title}</strong> in their Orderly Affairs Kit.
      </p>
      <p>
        Because you have immediate access, you can sign in to review the latest
        information when you are ready.
      </p>
      <p>
        <a href="{login_url}">Log in to Orderly Affairs</a>
      </p>
      <hr />
      <small>Orderly Affairs update notification</small>
    </div>
    """

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=nextkin["email"],
        subject=f"Orderly Affairs – {section_title} Updated",
        html_content=html,
    )

    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    sg.send(message)


async def notify_immediate_access_on_section_update(
    owner_id: str,
    section_id: str,
) -> None:
    from bson import ObjectId

    section_id = str(section_id)

    if section_id in SECTIONS_EXCLUDED_FROM_UPDATE_NOTIFICATIONS:
        return

    if not await _cooldown_allows_send(owner_id, section_id):
        return

    try:
        owner_oid = ObjectId(owner_id)
    except Exception:
        return

    owner = await users_collection.find_one({"_id": owner_oid, "role": "owner"})
    if not owner:
        return

    owner_key = str(owner["_id"])

    cursor = users_collection.find(
        {
            "owner_id": owner_key,
            "role": "nextkin",
            "immediate_access": True,
        }
    )

    async for nextkin in cursor:
        if not should_notify_nextkin_for_section(nextkin, section_id):
            continue

        try:
            await _send_section_update_email(
                nextkin=nextkin,
                owner=owner,
                section_id=section_id,
            )
        except Exception as exc:
            print(
                "⚠️ Section update notification failed:",
                section_id,
                nextkin.get("email"),
                exc,
            )
