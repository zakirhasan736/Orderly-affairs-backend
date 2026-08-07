"""Collections included in / excluded from daily user-data backups.

Vault section payloads stay encrypted at rest (`encrypted_data` / `encrypted_payload`).
Backups never decrypt — they archive Mongo documents as stored.
"""

# Durable owner / kit / family data (encrypted fields remain ciphertext).
BACKUP_COLLECTIONS: tuple[str, ...] = (
    "users",
    "kits",
    "letters",
    "sections",
    "nexrkinmessage",
    "onboarding_progress",
    "ai_documents",
    "ai_brain_settings",
    "ai_skill_examples",
    "support_threads",
    "support_messages",
    "feedback",
    "section_footprints",
    "pending_signups",
    # Admin / billing ops metadata (no vault body decrypt on restore either)
    "admin_audit_logs",
    "admin_coupons",
    "admin_notifications",
    "admin_broadcasts",
    "admin_role_defs",
    "admin_dsar_requests",
    "admin_legacy_requests",
    "admin_security_alerts",
    # Hashed identity tombstones after hard delete (no vault content).
    "deleted_accounts",
    "nok_letters",
    "scheduled_letters",
)

# Ephemeral / high-churn auth state — not useful for disaster recovery.
SKIP_COLLECTIONS: frozenset[str] = frozenset(
    {
        "otp",
        "otp_fraud_logs",
        "otp_verify_locks",
        "otp_send_locks",
        "sms_mfa_attempts",
        "auth_rate_limits",
        "refresh_tokens",
    }
)
