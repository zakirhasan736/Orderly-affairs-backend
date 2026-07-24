"""Deprecated: use section_expiry_scheduler (covers all sections)."""

from app.notifications.section_expiry_scheduler import (  # noqa: F401
    parse_expiry_date,
    process_insurance_expiry_reminders,
    process_section_expiry_reminders,
    start_insurance_expiry_scheduler,
    start_section_expiry_scheduler,
)
