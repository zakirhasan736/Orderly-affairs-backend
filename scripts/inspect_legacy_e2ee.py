"""Inspect legacy v2 section_data rows (ids only — no ciphertext)."""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()
os.environ.pop("AWS_SECRETS_MANAGER_LOADED", None)

from app.security.secrets_bootstrap import apply_aws_secrets_manager

apply_aws_secrets_manager(force=True)

from app.database import section_data_collection


async def main() -> None:
    cursor = section_data_collection.find(
        {
            "encrypted_data": {"$exists": True, "$ne": None},
            "$or": [
                {"encryption_version": {"$exists": False}},
                {"encryption_version": {"$ne": 3}},
            ],
        },
        {
            "owner_id": 1,
            "section_id": 1,
            "section_key": 1,
            "encryption_version": 1,
            "updated_at": 1,
        },
    )
    rows = await cursor.to_list(500)
    print(f"legacy_rows={len(rows)}")
    by_owner: dict[str, list] = {}
    for r in rows:
        oid = str(r.get("owner_id"))
        by_owner.setdefault(oid, []).append(
            {
                "section_id": r.get("section_id"),
                "section_key": r.get("section_key"),
                "ver": r.get("encryption_version"),
                "id": str(r.get("_id")),
            }
        )
    print(f"owners_with_legacy={len(by_owner)}")
    for oid, items in list(by_owner.items())[:20]:
        print(f"owner={oid[:8]}… count={len(items)} sections={[i['section_id'] for i in items]}")


if __name__ == "__main__":
    asyncio.run(main())
