"""SendGrid emails for any section expiry / renewal / deadline reminders."""

from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.notifications.email_layout import (
    email_cta_row,
    email_expiry_rows,
    escape,
    kit_url,
    paper_body,
    paper_hint,
    render_reminder_card,
)


def _is_renewal_style(field_label: str, item_label: str) -> bool:
    blob = f"{field_label} {item_label}".lower()
    return any(
        token in blob
        for token in (
            "renew",
            "maturity",
            "deadline",
            "tax",
            "mortgage",
            "loan",
            "lease",
            "due",
            "insurance",
            "policy",
        )
    )


def _section_code(section_title: str) -> str:
    mapping = {
        "Vital Information": "1-START",
        "Vehicles": "5-VEH",
        "Main Residence": "6-HOME",
        "Insurance Policies": "5-INS",
        "Organizations & Memberships": "8-ORG",
        "Charitable Contributions": "9-GIVE",
        "Bank Accounts": "10-BANK",
        "Legal Documents & Records": "20-LEGAL",
        "Estate Planning & Final Wishes": "21-ESTATE",
    }
    return mapping.get(section_title) or section_title[:8].upper()


def _short_date(expiry_date: str) -> str:
    text = (expiry_date or "").strip()
    if not text:
        return "Soon"
    # Prefer already-friendly strings; trim ISO timestamps.
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text)
        return dt.strftime("%b %d").replace(" 0", " ")
    except Exception:
        return text


def _subject(days: int, section_title: str, item_label: str, field_label: str) -> str:
    short = item_label or section_title
    verb = "Due" if _is_renewal_style(field_label, item_label) else "Expires"
    if days == 0:
        return f"{verb} today: {short}"
    if days == 1:
        return f"{verb} tomorrow: {short}"
    return f"{verb} in {days} days: {short}"


def _item_verb_label(field_label: str, item_label: str) -> str:
    renewal = _is_renewal_style(field_label, item_label)
    base = item_label or field_label or "Item"
    if renewal and "renew" not in base.lower():
        return f"{base} renews"
    if not renewal and "expir" not in base.lower():
        return f"{base} expires"
    return base


def _body(
    *,
    days: int,
    recipient_name: str,
    owner_name: str,
    section_title: str,
    item_label: str,
    field_label: str,
    expiry_date: str,
) -> str:
    _ = recipient_name
    renewal = _is_renewal_style(field_label, item_label)
    if days == 0:
        title = "Something in your kit is due today."
    elif days == 1:
        title = "Something in your kit is due tomorrow."
    elif renewal:
        title = "Something in your kit renews soon."
    else:
        title = "Something in your kit expires soon."

    rows = [
        (
            _section_code(section_title),
            _item_verb_label(field_label, item_label),
            _short_date(expiry_date),
        )
    ]

    if owner_name:
        intro = (
            f"A reminder from <b>{escape(owner_name)}</b>’s kit — open the "
            "section and update the date once this is handled so we stop "
            "reminding you."
        )
    else:
        intro = (
            "If this is already handled, open the section and update the date "
            "so we stop reminding you."
        )

    return render_reminder_card(
        schedule_label="Weekly Mon 08:00 · expiry watcher",
        title=title,
        preheader=f"{item_label or section_title} · {_short_date(expiry_date)}",
        body_html="".join(
            [
                email_expiry_rows(rows),
                paper_hint(intro),
                email_cta_row((kit_url(), "Open my kit")),
            ]
        ),
    )


def send_expiry_reminder_email(
    *,
    to_email: str,
    recipient_name: str,
    owner_name: str,
    section_title: str,
    item_label: str,
    field_label: str,
    expiry_date: str,
    days_before: int,
) -> None:
    if not to_email or not settings.SENDGRID_API_KEY:
        return

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=to_email,
        subject=_subject(days_before, section_title, item_label, field_label),
        html_content=_body(
            days=days_before,
            recipient_name=recipient_name,
            owner_name=owner_name,
            section_title=section_title,
            item_label=item_label,
            field_label=field_label,
            expiry_date=expiry_date,
        ),
    )
    sg.send(message)


def send_expiry_digest_email(
    *,
    to_email: str,
    items: list[tuple[str, str, str]],
) -> None:
    """Multi-item expiry digest matching the design comp.

    ``items``: list of (section_code, label, date_text).
    """
    if not to_email or not settings.SENDGRID_API_KEY or not items:
        return

    n = len(items)
    title = (
        "One thing in your kit expires soon."
        if n == 1
        else f"{n} things in your kit expire soon."
        if n != 2
        else "Two things in your kit expire soon."
    )
    html = render_reminder_card(
        schedule_label="Weekly Mon 08:00 · expiry watcher",
        title=title,
        preheader=title,
        body_html="".join(
            [
                email_expiry_rows(items),
                paper_hint(
                    "If these are already handled, open the section and update "
                    "the date so we stop reminding you."
                ),
                email_cta_row((kit_url(), "Open my kit")),
            ]
        ),
    )
    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=to_email,
        subject=title,
        html_content=html,
    )
    sg.send(message)


# Back-compat for older insurance-only imports
def send_insurance_expiry_email(**kwargs):
    send_expiry_reminder_email(
        to_email=kwargs.get("to_email") or "",
        recipient_name=kwargs.get("recipient_name") or "",
        owner_name=kwargs.get("owner_name") or "",
        section_title="Insurance Policies",
        item_label=kwargs.get("policy_label") or "Insurance policy",
        field_label="Policy expiry",
        expiry_date=kwargs.get("expiry_date") or "",
        days_before=int(kwargs.get("days_before") or 0),
    )
