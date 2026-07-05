"""Stable device fingerprint from User-Agent + client IP for high-risk auth events."""

from __future__ import annotations

import hashlib
import logging

from fastapi import Request

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def compute_device_fingerprint(request: Request) -> str:
    """Hash User-Agent and client IP into a stable, non-reversible fingerprint."""
    user_agent = request.headers.get("User-Agent", "")
    ip = _client_ip(request)
    raw = f"{user_agent}|{ip}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def log_device_fingerprint(
    request: Request,
    action: str,
    *,
    subject: str | None = None,
) -> str:
    fingerprint = compute_device_fingerprint(request)
    logger.info(
        "device_fingerprint action=%s subject=%s fingerprint=%s",
        action,
        subject or "",
        fingerprint,
    )
    return fingerprint
