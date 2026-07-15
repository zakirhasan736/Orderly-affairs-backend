from pydantic_settings import BaseSettings
from pydantic import EmailStr
from pathlib import Path
from datetime import timedelta
import os

class Settings(BaseSettings):
    # === Database ===
    MONGO_URL: str

    # === JWT ===
    JWT_ALGORITHM: str = "RS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # Target interval for RS256 key rotation (calendar/process; see app/security/KEY_ROTATION.md).
    JWT_KEY_ROTATION_DAYS: int = 90
    # Target interval for AES-256-GCM at-rest key rotation (annual or per policy).
    AES_KEY_ROTATION_DAYS: int = 365

    # === Auth rate limiting ===
    # Slightly more forgiving for MFA / reset flows behind Turnstile
    AUTH_RATE_LIMIT_MAX_ATTEMPTS: int = 20
    AUTH_RATE_LIMIT_WINDOW_MINUTES: int = 15

    # Load keys from either .env or /keys folder
    JWT_PRIVATE_KEY: str | None = None
    JWT_PUBLIC_KEY: str | None = None

    # === Email ===
    SENDGRID_API_KEY: str
    EMAIL_SENDER: EmailStr
    MESSAGES_FROM_EMAIL: EmailStr = "messages@orderly-affairs.com"

    # === Cloudinary ===
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str
    
    # === App Info ===
    APP_NAME: str = "Orderly Affairs"
    APP_ENV: str = "development"
    
    # === Stripe ===
    STRIPE_SECRET_KEY: str
    STRIPE_PRICE_MONTHLY: str
    STRIPE_PRICE_YEARLY: str
    TRIAL_DAYS: int = 15

    # === SMS (Twilio) ===
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    TWILIO_VERIFY_SERVICE_SID: str

    # === OTP Security ===
    OTP_CAPTCHA_ENABLED: bool = True
    TURNSTILE_SECRET_KEY: str | None = None
    OTP_ALLOWED_COUNTRIES: str = "*"
    OTP_PHONE_COOLDOWN_SECONDS: int = 60
    OTP_PHONE_MAX_PER_HOUR: int = 5
    OTP_PHONE_MAX_PER_DAY: int = 10
    OTP_EMAIL_COOLDOWN_SECONDS: int = 60
    OTP_EMAIL_MAX_PER_HOUR: int = 5
    OTP_EMAIL_MAX_PER_DAY: int = 10
    OTP_IP_MAX_PER_HOUR: int = 10
    OTP_IP_MAX_PER_DAY: int = 30
    OTP_SESSION_MAX_PER_HOUR: int = 5
    OTP_VERIFY_MAX_ATTEMPTS: int = 5
    OTP_VERIFY_LOCK_MINUTES: int = 30

    STRIPE_WEBHOOK_SECRET: str
     # === Base url Info ===
    FRONTEND_URL: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # ignore stray lines that caused earlier dotenv errors


# --- Initialize Settings ---
settings = Settings()


def nextkin_login_url() -> str:
    """Public Next-of-Kin sign-in page (frontend route)."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/next-kin"


def owner_login_url() -> str:
    """Owner sign-in page (logging in counts as an inactivity check-in)."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/login"

SEND_RETRY_BACKOFF = timedelta(minutes=10)
# If PEM files exist, load them (preferred for clean .env)
private_key_path = Path("keys/private.pem")
public_key_path = Path("keys/public.pem")

if private_key_path.exists() and public_key_path.exists():
    settings.JWT_PRIVATE_KEY = private_key_path.read_text()
    settings.JWT_PUBLIC_KEY = public_key_path.read_text()

# Print to confirm (optional debug)
if settings.APP_ENV == "development":
    print(f"Loaded config for {settings.APP_NAME}")
