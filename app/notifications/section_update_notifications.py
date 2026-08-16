from datetime import datetime, timedelta

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.auth.access_types import is_family_collaborator
from app.auth.family_access import family_has_dashboard_area
from app.auth.notification_prefs import (
    get_owner_notification_prefs,
    resolve_section_update_recipient_ids,
)
from app.config import nextkin_login_url, settings
from app.database import db, users_collection
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
from app.notifications.email_layout import (
    email_callout,
    escape,
    render_simple_email,
)

# Personal messages stay private. Every other section can notify people the owner picks.
SECTIONS_EXCLUDED_FROM_UPDATE_NOTIFICATIONS = frozenset({"4"})

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
    return level in {
        "full kit access",
        "full access",
        "full",
        "full dashboard access",
        "full dashboard",
    }


def _person_id(person: dict) -> str:
    return str(person.get("_id") or person.get("id") or "")


def is_eligible_update_recipient(person: dict) -> bool:
    if person.get("access_revoked"):
        return False
    if not str(person.get("email") or "").strip():
        return False
    if is_family_collaborator(person):
        return True
    return bool(person.get("immediate_access"))


def should_notify_person_for_section(person: dict, section_id: str) -> bool:
    if section_id in SECTIONS_EXCLUDED_FROM_UPDATE_NOTIFICATIONS:
        return False

    if is_family_collaborator(person):
        if person.get("access_revoked"):
            return False
        return family_has_dashboard_area(person, section_id)

    return should_notify_nextkin_for_section(person, section_id)


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
    owner_name = await resolve_owner_display_name(owner)
    nk_name = resolve_nextkin_display_name(nextkin)
    login_url = nextkin_login_url()

    html = render_simple_email(
        title=f"{section_title} updated",
        greeting_name=nk_name,
        paragraphs=[
            f"<strong>{escape(owner_name)}</strong> updated "
            f"<strong>{escape(section_title)}</strong> in their Orderly Affairs vault.",
            "The owner asked us to let you know so you can review the latest "
            "information when you are ready.",
        ],
        details=[("Section", section_title)],
        callout_html=email_callout(
            "Sign in when you're ready to review the latest details.",
            tone="info",
        ),
        cta_url=login_url,
        cta_label="Log in to Orderly Affairs",
        preheader=f"{owner_name} updated {section_title}",
    )

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
    prefs = get_owner_notification_prefs(owner)
    selected_ids = resolve_section_update_recipient_ids(prefs, section_id)
    selected_set = (
        {str(item) for item in selected_ids} if isinstance(selected_ids, list) else None
    )

    cursor = users_collection.find(
        {
            "owner_id": owner_key,
            "role": "nextkin",
        }
    )

    async for person in cursor:
        if not is_eligible_update_recipient(person):
            continue
        if selected_set is not None and _person_id(person) not in selected_set:
            continue

        try:
            await _send_section_update_email(
                nextkin=person,
                owner=owner,
                section_id=section_id,
            )
            try:
                from app.notifications.push_bridge import notify_web_push
                from app.notifications.email_layout import portal_url

                await notify_web_push(
                    person,
                    title="Vault section updated",
                    body=f"A section in the shared kit was updated. Open to review.",
                    tag=f"section-update-{section_id}",
                    url=portal_url(),
                    urgency="normal",
                )
            except Exception as push_exc:
                print("⚠️ Section update web push failed:", push_exc)
        except Exception as exc:
            print(
                "⚠️ Section update notification failed:",
                section_id,
                person.get("email"),
                exc,
            )
