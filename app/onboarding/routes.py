from app.security.token_resolver import decode_owner_or_nok_token
from fastapi import APIRouter, HTTPException, Header, Request
from datetime import datetime
from app.database import onboarding_progress

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


# -----------------------------------
# GET TOUR STATUS
# -----------------------------------
@router.get("/status")
async def get_tour_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = decode_owner_or_nok_token(request, authorization)

    doc = await onboarding_progress.find_one(
        {"user_id": user["sub"], "role": user["role"]}
    )

    if not doc:
        return {
            "version": None,
            "has_completed": False,
            "manually_started": False,
            "last_run_at": None,
        }

    doc["_id"] = str(doc["_id"])
    return doc


# -----------------------------------
# UPDATE TOUR STATUS
# -----------------------------------
@router.post("/status")
async def update_tour_status(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = decode_owner_or_nok_token(request, authorization)
    now = datetime.utcnow()

    update_fields = {
        "updated_at": now,
        "last_run_at": now,
    }

    if "version" in data:
        update_fields["version"] = data["version"]

    if "has_completed" in data:
        update_fields["has_completed"] = data["has_completed"]

    if "manually_started" in data:
        update_fields["manually_started"] = data["manually_started"]

    await onboarding_progress.update_one(
        {"user_id": user["sub"], "role": user["role"]},
        {
            "$set": update_fields,
            "$setOnInsert": {
                "created_at": now,
                "user_id": user["sub"],
                "role": user["role"],
            },
        },
        upsert=True,
    )

    return {"message": "Tour status updated successfully"}
