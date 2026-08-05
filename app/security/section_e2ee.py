"""Section E2EE (v3) — opaque client ciphertext; server never decrypts.

encryption_version:
  2 = server AES-256-GCM (legacy at-rest)
  3 = client E2EE (AES-GCM ciphertext from browser; no server key)
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.security.section_crypto import decrypt_section_data, encrypt_section_data

E2EE_VERSION = 3
LEGACY_VERSION = 2


def is_e2ee_doc(section: dict | None) -> bool:
    if not section:
        return False
    return int(section.get("encryption_version") or 0) == E2EE_VERSION


def is_e2ee_write_body(body: dict | None) -> bool:
    if not isinstance(body, dict):
        return False
    return bool(body.get("e2ee")) and isinstance(body.get("ciphertext"), str) and bool(
        body.get("ciphertext")
    )


def ciphertext_fingerprint(ciphertext: str) -> str:
    return hashlib.sha256(ciphertext.encode("utf-8")).hexdigest()


def present_section_for_api(
    owner_id: str,
    section_id: str,
    section_key: str,
    section: dict | None,
) -> dict[str, Any]:
    """API response: plaintext `data` (v2) or opaque `ciphertext` (v3)."""
    if not section or not section.get("encrypted_data"):
        return {}
    if is_e2ee_doc(section):
        return {
            "section_key": section_key or section.get("section_key"),
            "e2ee": True,
            "encryption_version": E2EE_VERSION,
            "ciphertext": section["encrypted_data"],
        }
    return {
        "section_key": section_key or section.get("section_key"),
        "encryption_version": int(section.get("encryption_version") or LEGACY_VERSION),
        "data": decrypt_section_data(owner_id, section_id, section["encrypted_data"]),
    }


def present_kit_section(owner_id: str, section: dict) -> dict[str, Any]:
    section_id = str(section.get("section_id") or "")
    base = {
        "id": section_id,
        "key": section.get("section_key"),
        "subsections": section.get("subsections", []),
        "updated_at": section.get("updated_at"),
    }
    if is_e2ee_doc(section):
        return {
            **base,
            "e2ee": True,
            "encryption_version": E2EE_VERSION,
            "ciphertext": section.get("encrypted_data") or "",
            "data": None,
        }
    try:
        data = decrypt_section_data(
            owner_id, section_id, section.get("encrypted_data") or ""
        )
    except Exception:
        data = {}
    return {
        **base,
        "encryption_version": int(section.get("encryption_version") or LEGACY_VERSION),
        "e2ee": False,
        "data": data,
    }


def prepare_write_blob(
    owner_id: str,
    section_id: str,
    body: dict,
) -> tuple[str, int, dict | None]:
    """
    Returns (encrypted_or_opaque_blob, encryption_version, plaintext_or_none).

    For v3, plaintext is None — server must not attempt decrypt.
    When E2EE_ENABLED is false, never accept opaque client ciphertext.
    """
    from app.config import settings

    if is_e2ee_write_body(body):
        if not bool(getattr(settings, "E2EE_ENABLED", False)):
            raise ValueError(
                "Client E2EE writes are disabled — use server AES-256-GCM section APIs"
            )
        return str(body["ciphertext"]), E2EE_VERSION, None
    # Strip transport keys if mixed
    clean = {k: v for k, v in body.items() if k not in ("e2ee", "ciphertext")}
    blob = encrypt_section_data(owner_id, section_id, clean)
    return blob, LEGACY_VERSION, clean
