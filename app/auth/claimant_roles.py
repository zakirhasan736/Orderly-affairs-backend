"""Who may start after-death verification (next of kin vs attorney/executor).

Family collaborators are never claimants. Every next of kin completes Didit
ID+selfie at first login, before the dashboard. After that they report a
passing, then upload a death certificate.
"""

from __future__ import annotations

import re
from typing import Any

from app.auth.access_types import ACCESS_TYPE_FAMILY, resolve_access_type

_LEGAL_ROLE_RE = re.compile(
    r"\b("
    r"attorney|lawyer|counsel|solicitor|"
    r"executor|executrix|"
    r"trustee|"
    r"personal\s+representative|"
    r"estate\s+(?:attorney|lawyer|counsel|representative|administrator)|"
    r"administrator|"
    r"power\s+of\s+attorney|poa"
    r")\b",
    re.IGNORECASE,
)


def is_family_claimant(user: dict | None) -> bool:
    return resolve_access_type(user) == ACCESS_TYPE_FAMILY


def is_attorney_or_executor(user: dict | None) -> bool:
    if not user or is_family_claimant(user):
        return False
    blob = " ".join(
        str(user.get(key) or "")
        for key in ("relationship", "full_name", "portal_role", "person_role")
    )
    return bool(_LEGAL_ROLE_RE.search(blob))


def claimant_kind_label(user: dict | None) -> str:
    from app.auth.portal_roles import role_label

    if is_family_claimant(user):
        return f"Family · {role_label((user or {}).get('portal_role'))}"
    legal = is_attorney_or_executor(user)
    immediate = bool(
        (user or {}).get("immediate_access")
        or (user or {}).get("access_timing") == "immediate"
    )
    role = "Attorney / executor" if legal else "Next of Kin"
    timing = "Immediate" if immediate else "After death"
    return f"{role} · {timing}"


def didit_purpose(user: dict | None) -> str:
    if is_attorney_or_executor(user):
        return "attorney_initiation"
    return "death_claim"


def public_claimant_flags(user: dict | None) -> dict[str, Any]:
    legal = is_attorney_or_executor(user)
    family = is_family_claimant(user)
    return {
        "is_attorney_or_executor": legal,
        "didit_before_report": not family,
        "claimant_kind": claimant_kind_label(user),
    }
