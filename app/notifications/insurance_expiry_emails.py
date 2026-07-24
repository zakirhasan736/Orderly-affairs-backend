"""SendGrid emails for insurance / registration expiry reminders."""

from enum import Enum

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


class InsuranceExpiryReminderDay(Enum):
    DAY_10 = 10
    DAY_5 = 5
    DAY_1 = 1
    DAY_0 = 0


def _subject(days: int, policy_label: str) -> str:
    if days == 0:
        return f"Insurance expiry today: {policy_label}"
    if days == 1:
        return f"Insurance expires tomorrow: {policy_label}"
    return f"Insurance expiry in {days} days: {policy_label}"


def _body(
    *,
    days: int,
    recipient_name: str,
    owner_name: str,
    policy_label: str,
    expiry_date: str,
    company: str | None,
) -> str:
    company_line = f"<p><b>Company:</b> {company}</p>" if company else ""

    if days == 0:
        timing = "expires <b>today</b>"
    elif days == 1:
        timing = "expires <b>tomorrow</b>"
    else:
        timing = f"expires in <b>{days} days</b>"

    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.5;color:#10213f">
      <p>Hi {recipient_name or "there"},</p>
      <p>
        This is a reminder that an insurance policy for
        <b>{owner_name}</b> {timing}.
      </p>
      <p><b>Policy:</b> {policy_label}</p>
      {company_line}
      <p><b>Expiry / registration end date:</b> {expiry_date}</p>
      <p>
        Please review the policy details in Orderly Affairs and renew or update
        coverage if needed.
      </p>
      <p style="color:#64748b;font-size:12px">
        Reminder schedule: 10 days → 5 days → 1 day → expiry day.
      </p>
    </div>
    """


def send_insurance_expiry_email(
    *,
    to_email: str,
    recipient_name: str,
    owner_name: str,
    policy_label: str,
    expiry_date: str,
    days_before: int,
    company: str | None = None,
) -> None:
    if not to_email or not settings.SENDGRID_API_KEY:
        return

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=to_email,
        subject=_subject(days_before, policy_label),
        html_content=_body(
            days=days_before,
            recipient_name=recipient_name,
            owner_name=owner_name,
            policy_label=policy_label,
            expiry_date=expiry_date,
            company=company,
        ),
    )
    sg.send(message)
