from fastapi import HTTPException

from app.billing.access import PAYMENT_LOCK_MESSAGE, is_billing_only


def enforce_billing(user: dict):
    """
    Soft check for /me-style endpoints: do not raise.
    Prefer billing_session_flags + frontend billing_only gate.
    Kept for backwards compatibility — use enforce_vault_access for vault APIs.
    """
    _ = user
    return


def enforce_billing_hard(user: dict):
    from app.billing.access import enforce_vault_access

    enforce_vault_access(user)
