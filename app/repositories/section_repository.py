from datetime import datetime
from app.database import section_data_collection


class SectionRepository:

    @staticmethod
    async def get(owner_id: str, section_id: str):
        return await section_data_collection.find_one({
            "owner_id": owner_id,
            "section_id": section_id
        })

    @staticmethod
    async def upsert(
        owner_id: str,
        section_id: str,
        section_key: str,
        encrypted_data: str,
        subsections: list[str],
    ):
        now = datetime.utcnow()

        await section_data_collection.update_one(
            {"owner_id": owner_id, "section_id": section_id},
            {
                "$set": {
                    "section_key": section_key,
                    "encrypted_data": encrypted_data,
                    "subsections": subsections,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

    @staticmethod
    async def delete(owner_id: str, section_id: str):
        await section_data_collection.delete_one({
            "owner_id": owner_id,
            "section_id": section_id
        })
