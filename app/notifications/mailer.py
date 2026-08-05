"""Outbound email via Amazon SES (replaces SendGrid).

All notification / OTP / invite mailers should call send_email() here so the
provider stays in one place.
"""

from __future__ import annotations

import logging
from email.utils import formataddr, parseaddr
from typing import Sequence

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)

_ses_client = None


def _region() -> str:
    return (
        (settings.SES_REGION or settings.AWS_REGION or "us-east-1").strip()
        or "us-east-1"
    )


def _ses():
    global _ses_client
    if _ses_client is not None:
        return _ses_client

    kwargs: dict = {"region_name": _region()}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    _ses_client = boto3.client("ses", **kwargs)
    return _ses_client


def _format_address(value: str) -> str:
    name, addr = parseaddr(str(value or "").strip())
    if not addr:
        return str(value or "").strip()
    if name:
        return formataddr((name, addr))
    return addr


def email_sending_configured() -> bool:
    """True when From address is set (SES uses AWS credentials already on settings)."""
    return bool(str(settings.EMAIL_SENDER or "").strip())


def send_email(
    *,
    to_emails: str | Sequence[str],
    subject: str,
    html_content: str,
    from_email: str | None = None,
    text_content: str | None = None,
) -> dict:
    """
    Send one HTML (and optional text) email through Amazon SES.

    Raises RuntimeError on SES / AWS failures so callers that previously
    relied on SendGrid exceptions keep the same control flow.
    """
    if not email_sending_configured():
        raise RuntimeError("EMAIL_SENDER is not configured")

    if isinstance(to_emails, str):
        destinations = [to_emails.strip()]
    else:
        destinations = [str(addr).strip() for addr in to_emails if str(addr).strip()]

    destinations = [addr for addr in destinations if addr]
    if not destinations:
        raise RuntimeError("No recipient email address")

    source = _format_address(from_email or str(settings.EMAIL_SENDER))
    body: dict = {"Html": {"Data": html_content, "Charset": "UTF-8"}}
    if text_content:
        body["Text"] = {"Data": text_content, "Charset": "UTF-8"}

    params: dict = {
        "Source": source,
        "Destination": {"ToAddresses": destinations},
        "Message": {
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": body,
        },
    }

    try:
        response = _ses().send_email(**params)
    except (ClientError, BotoCoreError) as exc:
        logger.exception("SES send_email failed to=%s subject=%s", destinations, subject)
        raise RuntimeError(f"SES email send failed: {exc}") from exc

    message_id = (response or {}).get("MessageId")
    logger.info(
        "SES email sent to=%s subject=%s message_id=%s",
        destinations,
        subject,
        message_id,
    )
    return response or {}
