"""
Put remaining secrets into SSM Parameter Store (/orderly-affairs/*).

Requires IAM: ssm:PutParameter on arn:aws:ssm:REGION:ACCOUNT:parameter/orderly-affairs/*

Usage (from repo root, with thin .env AWS keys loaded):

  python scripts/put_ssm_remaining_secrets.py

Reads values from (first match wins):
  1) current process env / .env
  2) .env.fullbackup-* if present (for migration only)

Generates BACKUP_ENCRYPTION_KEY if missing and prints it once so you can
store an offline copy. Does not print other secret values.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

NAMES = (
    "BACKUP_ENCRYPTION_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "DIDIT_API_KEY",
    "DIDIT_WEBHOOK_SECRET",
    "DIDIT_WORKFLOW_ID",
    "DIDIT_APPLICATION_ID",
    "CLOUDINARY_CLOUD_NAME",
    "TWILIO_PHONE_NUMBER",
)


def _backup_env() -> dict[str, str | None]:
    matches = sorted(ROOT.glob(".env.fullbackup-*"), reverse=True)
    if not matches:
        return {}
    return dict(dotenv_values(matches[0]))


def main() -> int:
    import boto3
    from botocore.exceptions import ClientError

    region = os.getenv("AWS_REGION") or "us-east-1"
    client = boto3.client(
        "ssm",
        region_name=region,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )
    backup = _backup_env()
    generated_backup_key = False

    for name in NAMES:
        value = (os.getenv(name) or backup.get(name) or "").strip()
        if name == "BACKUP_ENCRYPTION_KEY" and not value:
            value = base64.b64encode(secrets.token_bytes(32)).decode()
            generated_backup_key = True
            print(
                "Generated BACKUP_ENCRYPTION_KEY (store offline + in SSM):\n"
                f"  {value}\n"
            )
        if not value:
            print(f"SKIP {name} (empty)")
            continue
        path = f"/orderly-affairs/{name}"
        try:
            client.put_parameter(
                Name=path,
                Value=value,
                Type="SecureString",
                Overwrite=True,
            )
            print(f"OK  {path}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            print(f"FAIL {path}: {code} — add ssm:PutParameter (see docs/SECRETS_MANAGER.md)")
            if generated_backup_key and name == "BACKUP_ENCRYPTION_KEY":
                print(
                    "  Paste the generated key into AWS Console → SSM → "
                    "Create parameter /orderly-affairs/BACKUP_ENCRYPTION_KEY"
                )
            return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
