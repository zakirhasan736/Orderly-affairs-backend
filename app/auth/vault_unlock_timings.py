"""Vault unlock timings.

After-death owner protection is a fixed 168 hours (see after_death_policy.py).
The older 24-hour / 5–7-day windows below are LEGACY / NOT used for after-death
authorization. Path C check-in constants remain unused unless that feature is enabled.
Living NOK release is a separate flow (immediate_access_grant.py).
"""

from datetime import timedelta

# Shared pipeline Step 4 — owner notification window
OWNER_WINDOW_WHEN_DOCUMENT_AND_MORTALITY_AGREE = timedelta(hours=24)
OWNER_WINDOW_INACTIVITY_ONLY = timedelta(days=5)  # spec: 5–7 days
# Certificate path (NOK / attorney uploaded a death certificate for review)
OWNER_CERTIFICATE_WAIT = timedelta(days=7)
OWNER_WAIT_REMINDER_EVERY = timedelta(days=2)
OWNER_WAIT_REMINDER_OFFSETS = (timedelta(days=2), timedelta(days=4), timedelta(days=6))

# Path C check-in (fixed for every owner, not configurable)
CHECK_IN_CADENCE = timedelta(days=30)
OVERDUE_REMINDER_DAYS = (0, 7, 13)  # day 0 / 7 / 13 of being overdue
MISSED_CHECK_IN_AFTER = timedelta(days=14)

# Path C "confirmed deceased" — time to produce a certificate
CERTIFICATE_AFTER_CONFIRMED_DECEASED = timedelta(weeks=3)

# Manual review follow-up SLA (failed/inconclusive document, mortality, or ID)
MANUAL_REVIEW_FOLLOW_UP = timedelta(hours=48)

# Step 6b — admin release queue
ADMIN_RELEASE_ESCALATE_AFTER = timedelta(hours=24)

# Step 6c — claim link after admin release
CLAIM_TOKEN_TTL = timedelta(hours=72)
