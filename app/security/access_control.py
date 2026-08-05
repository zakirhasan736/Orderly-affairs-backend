from fastapi import HTTPException

from app.auth.access_types import is_nextkin_collaborator

# NOK survivor portal — management sections blocked at API (see SECURITY_MODEL.md).
NOK_HIDDEN_SECTION_IDS = frozenset({"2", "3", "4"})


def assert_section_read_access(user: dict, section_id: str):
    """
    Enforces read access for a given section_id (e.g. '1', '7')
    """

    # Owner = full access
    if user["role"] == "owner":
        return

    if user["role"] != "nextkin":
        raise HTTPException(status_code=403, detail="Invalid role")

    # Must be approved
    if not user.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    # ABAC: NOK principal cannot read owner-management sections via survivor portal.
    if is_nextkin_collaborator(user):
        sid = str(section_id or "").strip()
        parent = "".join(ch for ch in sid if ch.isdigit()) or sid
        if sid in NOK_HIDDEN_SECTION_IDS or parent in NOK_HIDDEN_SECTION_IDS:
            raise HTTPException(
                status_code=403,
                detail="This section is not available in the Next-of-Kin portal",
            )

    # Full vault / dashboard access (family uses Full Kit synonym in DB)
    level = user.get("access_level") or ""
    if level in ("Full Kit Access", "Full Dashboard Access"):
        return

    # Section-specific / area-specific access
    allowed = [str(x) for x in (user.get("authorized_sections") or [])]
    sid = str(section_id)
    if sid in allowed:
        return

    # Parent section digits: granting "5" covers "5", granting "5A" still
    # implies interest in vehicles when checking section "5".
    parent = "".join(ch for ch in sid if ch.isdigit()) or sid
    for entry in allowed:
        entry_parent = "".join(ch for ch in entry if ch.isdigit()) or entry
        if entry_parent == parent:
            return

    raise HTTPException(
        status_code=403,
        detail=f"No access to section {section_id}",
    )


def nok_has_section_access(user: dict, section_id: str) -> bool:
    """Non-throwing check — mirrors assert_section_read_access."""
    try:
        assert_section_read_access(user, section_id)
        return True
    except HTTPException:
        return False
