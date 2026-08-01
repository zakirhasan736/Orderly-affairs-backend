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
section_data_collection = db["sections"] 
messageofnextkin_collection = db["nexrkinmessage"] 
onboarding_progress = db["onboarding_progress"]
ai_documents_collection = db["ai_documents"]
# Admin-only skill corpus (silent fill logging; no owner UI yet)
ai_brain_settings_collection = db["ai_brain_settings"]
ai_skill_examples_collection = db["ai_skill_examples"]
refresh_tokens_collection = db["refresh_tokens"]
auth_rate_limits_collection = db["auth_rate_limits"]

support_threads_collection = db["support_threads"]
support_messages_collection = db["support_messages"]
feedback_collection = db["feedback"]

if settings.APP_ENV == "development":
    print("MongoDB connected successfully")
