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
    # NOK / family access tokens (shorter than owner).
    NOK_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10
    # Full Kit NOK / full dashboard family — tighter live session window.
    NOK_FULL_KIT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 5
    # Target interval for RS256 key rotation (calendar/process; see app/security/KEY_ROTATION.md).
    JWT_KEY_ROTATION_DAYS: int = 90
    # Target interval for AES-256-GCM at-rest key rotation (annual or per policy).
    AES_KEY_ROTATION_DAYS: int = 365

    # === Auth rate limiting ===
    # Short windows — never multi-hour locks for login / OTP
    AUTH_RATE_LIMIT_MAX_ATTEMPTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_MINUTES: int = 15
    # Absolute ceiling for any Retry-After (OTP / auth / verify)
    AUTH_RATE_LIMIT_MAX_LOCK_SECONDS: int = 1800  # 30 minutes
    AUTH_RATE_LIMIT_FIRST_LOCK_SECONDS: int = 45

    # Load keys from either .env or /keys folder
    JWT_PRIVATE_KEY: str | None = None
    JWT_PUBLIC_KEY: str | None = None
    # Previous RS256 public key kept during JWT rotation overlap (verify-only).
    JWT_PREVIOUS_PUBLIC_KEY: str | None = None
    # Optional previous AES key (base64) for decrypt during rotation overlap.
    # Prefer env AES_256_KEY_PREVIOUS (read in crypto.py); this mirrors config docs.
    AES_256_KEY_PREVIOUS: str | None = None

    # === Email ===
    SENDGRID_API_KEY: str
    EMAIL_SENDER: EmailStr
    MESSAGES_FROM_EMAIL: EmailStr = "messages@orderly-affairs.com"
    # Absolute HTTPS logo URL for HTML emails. Leave blank to use Cloudinary default
    # (email clients cannot load localhost / private image URLs).
    EMAIL_LOGO_URL: str | None = None
    # Optional public support phone shown in NOK letter footers
    SUPPORT_PHONE: str | None = None

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
    # Annual signup tiers (Advantage sale price $199). Blank → YEARLY fallback.
    STRIPE_PRICE_ESSENTIALS: str | None = None
    STRIPE_PRICE_ADVANTAGE: str | None = None
    TRIAL_DAYS: int = 14

    # === SMS (Twilio) ===
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str
    TWILIO_VERIFY_SERVICE_SID: str

    # === OTP Security ===
    OTP_CAPTCHA_ENABLED: bool = True
    TURNSTILE_SECRET_KEY: str | None = None
    OTP_ALLOWED_COUNTRIES: str = "*"
    OTP_PHONE_COOLDOWN_SECONDS: int = 45
    OTP_PHONE_MAX_PER_HOUR: int = 20
    OTP_PHONE_MAX_PER_DAY: int = 60
    OTP_EMAIL_COOLDOWN_SECONDS: int = 45
    # Burst window: after too many sends, wait at most this window (not hours/days)
    OTP_BURST_WINDOW_MINUTES: int = 15
    OTP_EMAIL_MAX_PER_BURST: int = 6
    OTP_EMAIL_MAX_PER_HOUR: int = 20
    OTP_EMAIL_MAX_PER_DAY: int = 60
    OTP_IP_MAX_PER_HOUR: int = 60
    OTP_IP_MAX_PER_DAY: int = 200
    OTP_SESSION_MAX_PER_HOUR: int = 30
    OTP_VERIFY_MAX_ATTEMPTS: int = 8
    OTP_VERIFY_LOCK_MINUTES: int = 15

    STRIPE_WEBHOOK_SECRET: str
    # === Base url Info ===
    FRONTEND_URL: str
    # Optional shared cookie domain, e.g. .orderly-affairs.com
    # (so portal middleware can read cookies set by api.*)
    COOKIE_DOMAIN: str | None = None
    # Double-submit CSRF for cookie-authenticated mutating API calls.
    CSRF_PROTECTION_ENABLED: bool = True

    # Comma-separated owner emails allowed into System Owner Admin (/admin/login).
    ADMIN_EMAILS: str = ""
    # Optional first-boot bootstrap only. Leave unset — never ship a default password.
    # If both are set and the email is missing, creates that admin once (does not reset).
    ADMIN_DEFAULT_EMAIL: str | None = None
    ADMIN_DEFAULT_PASSWORD: str | None = None
    # When True, admin-flagged owner cookies can hit /admin APIs.
    # Default: allowed only in development. Set true/false to override.
    ADMIN_ALLOW_OWNER_COOKIE_FALLBACK: bool | None = None

    # === Document vault (AI autofill uploads on VPS disk) ===
    # Production: /var/storage/vault  |  Local: storage/vault (project-relative)
    VAULT_ROOT: str = "storage/vault"
    # Hard ceiling for all users combined (default 100 GB).
    VAULT_GLOBAL_QUOTA_GB: float = 100.0
    # Default per-owner cap; overridden by user.enterprise_limits.storage_gb when set.
    VAULT_USER_QUOTA_MB: float = 5120.0  # 5 GB
    # Max size of a single AI upload.
    AI_UPLOAD_MAX_MB: float = 15.0
    # 0 = keep forever (vault). >0 = expire uploads after N minutes.
    AI_UPLOAD_TTL_MINUTES: int = 0

    # === Weekly security monitoring ===
    WEEKLY_SECURITY_MONITOR_ENABLED: bool = True
    # APScheduler day_of_week: mon..sun
    WEEKLY_SECURITY_MONITOR_DAY: str = "sun"
    WEEKLY_SECURITY_MONITOR_HOUR: int = 4
    WEEKLY_SECURITY_MONITOR_MINUTE: int = 30

    # === Client-side E2EE for vault sections (encryption_version 3) ===
    # When true: clients may store opaque ciphertext; server cannot decrypt v3.
    E2EE_ENABLED: bool = True

    # === Daily encrypted backups (Mongo user data as stored ciphertext) ===
    BACKUP_ENABLED: bool = True
    # Local directory for daily packages (gitignored under /storage/).
    BACKUP_ROOT: str = "storage/backups"
    BACKUP_CRON_HOUR: int = 3
    BACKUP_CRON_MINUTE: int = 0
    BACKUP_RETENTION_DAYS: int = 14
    # Include on-disk VAULT_ROOT files in the package (can be large).
    BACKUP_INCLUDE_VAULT_FILES: bool = False
    # Separate 32-byte key (base64). If unset, falls back to AES_256_KEY.
    # Prefer a dedicated offline key for disaster recovery.
    BACKUP_ENCRYPTION_KEY: str | None = None
    # Optional AWS S3 upload (enable bucket versioning in AWS console).
    BACKUP_S3_ENABLED: bool = False
    BACKUP_S3_BUCKET: str | None = None
    BACKUP_S3_PREFIX: str = "orderly-affairs/backups"
    BACKUP_S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None

    class Config:
        env_file = ".env"
        extra = "ignore"  # ignore stray lines that caused earlier dotenv errors

    @property
    def VAULT_GLOBAL_QUOTA_BYTES(self) -> int:
        return int(float(self.VAULT_GLOBAL_QUOTA_GB) * (1024**3))

    @property
    def VAULT_USER_QUOTA_BYTES(self) -> int:
        return int(float(self.VAULT_USER_QUOTA_MB) * (1024**2))

    @property
    def AI_UPLOAD_MAX_BYTES(self) -> int:
        return int(float(self.AI_UPLOAD_MAX_MB) * (1024**2))

    @property
    def allow_owner_cookie_admin_fallback(self) -> bool:
        """
        Allow admin-flagged owner sessions to call /admin APIs.

        Production default: False (admin cookie / admin Bearer only).
        Development default: True (legacy convenience).
        Override with ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=true|false.
        """
        if self.ADMIN_ALLOW_OWNER_COOKIE_FALLBACK is not None:
            return bool(self.ADMIN_ALLOW_OWNER_COOKIE_FALLBACK)
        return self.APP_ENV == "development"


# --- Initialize Settings ---
settings = Settings()


def family_dashboard_login_url() -> str:
    """Family collaborator sign-in — separate session from the owner; lands on owner dashboard."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/family/login"


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
previous_public_key_path = Path("keys/public.previous.pem")

if private_key_path.exists() and public_key_path.exists():
    settings.JWT_PRIVATE_KEY = private_key_path.read_text()
    settings.JWT_PUBLIC_KEY = public_key_path.read_text()

if not settings.JWT_PREVIOUS_PUBLIC_KEY and previous_public_key_path.exists():
    settings.JWT_PREVIOUS_PUBLIC_KEY = previous_public_key_path.read_text()

# Print to confirm (optional debug)
if settings.APP_ENV == "development":
    print(f"Loaded config for {settings.APP_NAME}")