from fastapi import HTTPException


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
