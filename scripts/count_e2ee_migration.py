"""Count encryption_version distribution in section_data (no secret values)."""
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
    total = await section_data_collection.count_documents(
        {"encrypted_data": {"$exists": True, "$ne": None}}
    )
    v3 = await section_data_collection.count_documents(
        {
            "encryption_version": 3,
            "encrypted_data": {"$exists": True, "$ne": None},
        }
    )
    v2 = await section_data_collection.count_documents(
        {
            "encrypted_data": {"$exists": True, "$ne": None},
            "$or": [
                {"encryption_version": {"$exists": False}},
                {"encryption_version": {"$ne": 3}},
            ],
        }
    )
    print(f"total={total} v3={v3} v2={v2}")
    rows = await section_data_collection.aggregate(
        [
            {
                "$match": {
                    "encrypted_data": {"$exists": True, "$ne": None},
                    "$or": [
                        {"encryption_version": {"$exists": False}},
                        {"encryption_version": {"$ne": 3}},
                    ],
                }
            },
            {"$group": {"_id": "$section_id", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ]
    ).to_list(100)
    print("legacy_by_section_id=", rows)


if __name__ == "__main__":
    asyncio.run(main())
