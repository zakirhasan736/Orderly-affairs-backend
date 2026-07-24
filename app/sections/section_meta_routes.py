from fastapi import APIRouter, Header, HTTPException, Request

from app.database import section_data_collection, users_collection
from app.security.token_resolver import decode_owner_or_nok_token

router = APIRouter(prefix="/sections", tags=["Section meta"])


@router.get("/updated-at")
async def get_sections_updated_at(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return last updated timestamps for all kit sections belonging to the owner."""
    decoded = decode_owner_or_nok_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner only")

    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"},
    )
    if not owner:
        raise HTTPException(status_code=401)

    owner_id = str(owner["_id"])
    cursor = section_data_collection.find(
        {"owner_id": owner_id},
        {"section_id": 1, "updated_at": 1},
    )

    sections: dict[str, str] = {}
    async for doc in cursor:
        section_id = str(doc.get("section_id") or "")
        updated_at = doc.get("updated_at")
        if not section_id or not updated_at:
            continue
        if hasattr(updated_at, "isoformat"):
            sections[section_id] = updated_at.isoformat() + (
                "Z" if updated_at.tzinfo is None else ""
            )
        else:
            sections[section_id] = str(updated_at)

    return {"sections": sections}
