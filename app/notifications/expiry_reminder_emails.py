"""SendGrid emails for any section expiry / renewal / deadline reminders."""

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


def _is_renewal_style(field_label: str, item_label: str) -> bool:
    blob = f"{field_label} {item_label}".lower()
    return any(token in blob for token in (
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
    ))


def _subject(days: int, section_title: str, item_label: str, field_label: str) -> str:
    short = item_label or section_title
    verb = "Due" if _is_renewal_style(field_label, item_label) else "Expires"
    if days == 0:
        return f"{verb} today: {short}"
    if days == 1:
        return f"{verb} tomorrow: {short}"
    return f"{verb} in {days} days: {short}"


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
    renewal = _is_renewal_style(field_label, item_label)
    if days == 0:
        timing = "is due <b>today</b>" if renewal else "expires <b>today</b>"
    elif days == 1:
        timing = "is due <b>tomorrow</b>" if renewal else "expires <b>tomorrow</b>"
    else:
        timing = (
            f"is due in <b>{days} days</b>"
            if renewal
            else f"expires in <b>{days} days</b>"
        )

    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.5;color:#10213f">
      <p>Hi {recipient_name or "there"},</p>
      <p>
        This is a reminder that an item in <b>{owner_name}</b>'s
        Orderly Affairs kit {timing}.
      </p>
      <p><b>Section:</b> {section_title}</p>
      <p><b>Item:</b> {item_label}</p>
      <p><b>Field:</b> {field_label}</p>
      <p><b>Deadline / renewal date:</b> {expiry_date}</p>
      <p>
        Please open Orderly Affairs, review the details, and renew, pay, or
        update if needed (insurance, taxes, loans, leases, and other deadlines).
      </p>
      <p style="color:#64748b;font-size:12px">
        Reminder schedule: 10 days → 5 days → 1 day → due / expiry day
        (same countdown pattern as trial reminders).
      </p>
    </div>
    """


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
