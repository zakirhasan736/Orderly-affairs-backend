from motor.motor_asyncio import AsyncIOMotorClient
import certifi
from app.config import settings

from app.config import settings

# Secure certificate bundle
ca = certifi.where()

# Async MongoDB client with TLS
client = AsyncIOMotorClient(
    settings.MONGO_URL,
    tls=True,
    tlsCAFile=ca,
    tlsAllowInvalidCertificates=False,
    serverSelectionTimeoutMS=10000
)

db = client["orderly_affairs"]

# ✅ Define all collections you use
users_collection = db["users"]
pending_signup_collection = db["pending_signups"]
sms_mfa_attempts_collection = db["sms_mfa_attempts"]
otp_fraud_logs_collection = db["otp_fraud_logs"]
otp_verify_locks_collection = db["otp_verify_locks"]
otp_send_locks_collection = db["otp_send_locks"]
otp_collection = db["otp"] 
kits_collection = db["kits"]  
letters_collection = db["letters"]
nok_letters_collection = db["nok_letters"]
scheduled_letters_collection = db["scheduled_letters"]
section_data_collection = db["sections"] 
messageofnextkin_collection = db["nexrkinmessage"] 
onboarding_progress = db["onboarding_progress"]
ai_documents_collection = db["ai_documents"]
# Admin-only skill corpus (silent fill logging; no owner UI yet)
ai_brain_settings_collection = db["ai_brain_settings"]
ai_skill_examples_collection = db["ai_skill_examples"]
refresh_tokens_collection = db["refresh_tokens"]
auth_rate_limits_collection = db["auth_rate_limits"]
# Hashed identity retained after hard account delete (rejoin detection).
deleted_accounts_collection = db["deleted_accounts"]

support_threads_collection = db["support_threads"]
support_messages_collection = db["support_messages"]
feedback_collection = db["feedback"]
section_footprints_collection = db["section_footprints"]
access_logs_collection = db["access_logs"]
vault_audit_logs_collection = db["vault_audit_logs"]
vault_zk_fields_collection = db["vault_zk_fields"]

# System-owner admin panel
admin_audit_logs_collection = db["admin_audit_logs"]
admin_coupons_collection = db["admin_coupons"]
admin_notifications_collection = db["admin_notifications"]
admin_broadcasts_collection = db["admin_broadcasts"]
admin_role_defs_collection = db["admin_role_defs"]
admin_dsar_collection = db["admin_dsar_requests"]
admin_legacy_collection = db["admin_legacy_requests"]
admin_security_alerts_collection = db["admin_security_alerts"]

if settings.is_development:
    print("MongoDB connected successfully")
