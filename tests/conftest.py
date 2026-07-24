"""
Pytest bootstrap — set required secrets before app modules import crypto.
"""

from __future__ import annotations

import base64
import os

# Must run before any `app.*` import during collection.
os.environ.setdefault(
    "AES_256_KEY",
    base64.b64encode(b"0" * 32).decode("ascii"),
)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("OTP_CAPTCHA_ENABLED", "false")
# HS256 test keys must match for encode/decode in unit tests.
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_PRIVATE_KEY", "orderly-test-jwt-secret-key")
os.environ.setdefault("JWT_PUBLIC_KEY", "orderly-test-jwt-secret-key")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017/orderly_test")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "asyncio: mark test as asyncio",
    )
