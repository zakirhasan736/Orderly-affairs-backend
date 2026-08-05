"""Re-export section ACL helpers (canonical implementation in access_control)."""

from app.security.access_control import (  # noqa: F401
    assert_section_read_access,
    nok_has_section_access,
)
