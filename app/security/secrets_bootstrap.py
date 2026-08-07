"""Load production secrets from AWS into os.environ.

Supports (Hostinger VPS friendly):
  1) SSM Parameter Store path  e.g. /orderly-affairs/AES_256_KEY  (SecureString)
  2) Secrets Manager JSON secret (optional alternate)

Thin .env on the VPS keeps only AWS bootstrap keys + path/id + non-secret config.

Must run before Settings() and before modules that read os.getenv("AES_256_KEY").
"""

from __future__ import annotations

import json
import os
from typing import Any

# Env keys the app understands.
MANAGED_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "MONGO_URL",
        "AES_256_KEY",
        "AES_256_KEY_PREVIOUS",
        "BACKUP_ENCRYPTION_KEY",
        "JWT_PRIVATE_KEY",
        "JWT_PUBLIC_KEY",
        "JWT_PREVIOUS_PUBLIC_KEY",
        "SENDGRID_API_KEY",
        "EMAIL_SENDER",
        "MESSAGES_FROM_EMAIL",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "TWILIO_VERIFY_SERVICE_SID",
        "TURNSTILE_SECRET_KEY",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_MONTHLY",
        "STRIPE_PRICE_YEARLY",
        "STRIPE_PRICE_ESSENTIALS",
        "STRIPE_PRICE_ADVANTAGE",
        "OPENAI_API_KEY",
        "OWN_MODEL_API_KEY",
        "ADMIN_DEFAULT_EMAIL",
        "ADMIN_DEFAULT_PASSWORD",
        "ADMIN_EMAILS",
        # Web Push (VAPID) — PEM private key may use literal \n in SSM
        "VAPID_PUBLIC_KEY",
        "VAPID_PRIVATE_KEY",
        "VAPID_SUBJECT",
    }
)

# Parameter leaf name (or JSON key) → app env name.
SECRET_KEY_ALIASES: dict[str, str] = {
    "MONGODB_URI": "MONGO_URL",
    "MONGO_URI": "MONGO_URL",
    "MONGODB_URL": "MONGO_URL",
}


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_secret_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if "\\n" in text and "BEGIN" in text:
        text = text.replace("\\n", "\n")
    return text


def _canonical_env_name(raw_key: str) -> str | None:
    name = str(raw_key or "").strip()
    if not name:
        return None
    # /orderly-affairs/AES_256_KEY → AES_256_KEY
    if "/" in name:
        name = name.rstrip("/").split("/")[-1]
    name = SECRET_KEY_ALIASES.get(name, name)
    if name in MANAGED_SECRET_KEYS:
        return name
    return None


def _boto_session_kwargs(region: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"region_name": region}
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID")
        kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY")
    return kwargs


def fetch_ssm_parameter_path(path: str, *, region: str) -> dict[str, str]:
    """Load all SecureString/String parameters under a path (e.g. /orderly-affairs/)."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for AWS Parameter Store. Install with: pip install boto3"
        ) from exc

    prefix = path if path.endswith("/") else f"{path}/"
    client = boto3.client("ssm", **_boto_session_kwargs(region))
    out: dict[str, str] = {}
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {
                "Path": prefix,
                "Recursive": True,
                "WithDecryption": True,
                "MaxResults": 10,
            }
            if token:
                kwargs["NextToken"] = token
            page = client.get_parameters_by_path(**kwargs)
            for param in page.get("Parameters") or []:
                name = str(param.get("Name") or "")
                value = param.get("Value")
                env_name = _canonical_env_name(name)
                if not env_name:
                    continue
                text = _normalize_secret_value(value).strip()
                if text:
                    out[env_name] = text
            token = page.get("NextToken")
            if not token:
                break
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to load SSM parameters under '{prefix}': {exc}"
        ) from exc

    return out


def fetch_secrets_manager_payload(secret_id: str, *, region: str) -> dict[str, Any]:
    """Optional: one JSON secret in Secrets Manager."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for AWS Secrets Manager. Install with: pip install boto3"
        ) from exc

    client = boto3.client("secretsmanager", **_boto_session_kwargs(region))
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to load secret '{secret_id}' from AWS Secrets Manager: {exc}"
        ) from exc

    raw = response.get("SecretString")
    if not raw and response.get("SecretBinary"):
        raw = response["SecretBinary"].decode("utf-8")
    if not raw:
        raise RuntimeError(f"Secret '{secret_id}' has empty SecretString")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Secret '{secret_id}' must be a JSON object of KEY=value pairs"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Secret '{secret_id}' JSON must be an object")
    return payload


def _apply_payload(payload: dict[str, Any], *, override: bool) -> int:
    applied = 0
    for key, value in payload.items():
        name = _canonical_env_name(str(key))
        if not name:
            continue
        text = _normalize_secret_value(value).strip()
        if not text:
            continue
        if not override and os.getenv(name):
            continue
        os.environ[name] = text
        applied += 1
    return applied


def apply_aws_secrets_manager(*, force: bool = False) -> bool:
    """
    Merge AWS secrets into os.environ.

    Priority:
      1) SSM path AWS_SSM_PARAMETER_PATH=/orderly-affairs/
      2) Secrets Manager JSON AWS_SECRETS_MANAGER_SECRET_ID=...

    Returns True when at least one AWS source was configured and loaded.
    """
    enabled = _truthy(os.getenv("AWS_SECRETS_MANAGER_ENABLED"), default=True)
    if not enabled and not force:
        return False

    if not force and os.getenv("AWS_SECRETS_MANAGER_LOADED") == "true":
        return True

    region = (
        os.getenv("AWS_SECRETS_MANAGER_REGION")
        or os.getenv("AWS_SSM_REGION")
        or os.getenv("AWS_REGION")
        or "us-east-1"
    ).strip()

    ssm_path = (
        os.getenv("AWS_SSM_PARAMETER_PATH")
        or os.getenv("SSM_PARAMETER_PATH")
        or ""
    ).strip()
    secret_id = (
        os.getenv("AWS_SECRETS_MANAGER_SECRET_ID")
        or os.getenv("SECRETS_MANAGER_SECRET_ID")
        or ""
    ).strip()

    # When an AWS source is configured, prefer SSM over stale thin-.env leftovers.
    override = _truthy(
        os.getenv("AWS_SECRETS_MANAGER_OVERRIDE"),
        default=bool(ssm_path or secret_id),
    )

    if not ssm_path and not secret_id:
        return False

    applied = 0
    sources: list[str] = []

    if ssm_path:
        payload = fetch_ssm_parameter_path(ssm_path, region=region)
        applied += _apply_payload(payload, override=override)
        sources.append(f"ssm:{ssm_path} ({len(payload)} params)")

    if secret_id:
        payload = fetch_secrets_manager_payload(secret_id, region=region)
        applied += _apply_payload(payload, override=override)
        sources.append(f"secretsmanager:{secret_id}")

    env = (os.getenv("APP_ENV") or "development").strip().lower()
    if env in {"production", "prod", "staging"} and applied < 1:
        raise RuntimeError(
            "AWS secrets configured but applied=0 in production/staging. "
            "Check IAM GetParametersByPath and parameter path."
        )

    os.environ["AWS_SECRETS_MANAGER_LOADED"] = "true"
    os.environ["AWS_SECRETS_MANAGER_APPLIED_COUNT"] = str(applied)
    print(
        "Loaded secrets from AWS "
        f"(applied={applied}, region={region}, override={override}, "
        f"sources={'; '.join(sources)})"
    )
    return True