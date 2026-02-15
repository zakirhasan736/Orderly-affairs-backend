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

    # Full access
    if user.get("access_level") == "Full Kit Access":
        return

    # Section-specific access
    allowed = user.get("authorized_sections", [])
    if section_id in allowed:
        return

    raise HTTPException(
        status_code=403,
        detail=f"No access to section {section_id}",
    )
