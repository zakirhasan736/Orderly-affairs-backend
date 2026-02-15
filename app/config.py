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

    # Load keys from either .env or /keys folder
    JWT_PRIVATE_KEY: str | None = None
    JWT_PUBLIC_KEY: str | None = None

    # === Email ===
    SENDGRID_API_KEY: str
    EMAIL_SENDER: EmailStr

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
    
    STRIPE_WEBHOOK_SECRET: str
     # === Base url Info ===
    FRONTEND_URL: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # ignore stray lines that caused earlier dotenv errors


# --- Initialize Settings ---
settings = Settings()

SEND_RETRY_BACKOFF = timedelta(minutes=10)
# If PEM files exist, load them (preferred for clean .env)
private_key_path = Path("keys/private.pem")
public_key_path = Path("keys/public.pem")

if private_key_path.exists() and public_key_path.exists():
    settings.JWT_PRIVATE_KEY = private_key_path.read_text()
    settings.JWT_PUBLIC_KEY = public_key_path.read_text()

# Print to confirm (optional debug)
if settings.APP_ENV == "development":
    print(f"✅ Loaded config for {settings.APP_NAME}")
