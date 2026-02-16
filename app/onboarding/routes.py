from fastapi import APIRouter, HTTPException, Header
from datetime import datetime
from app.database import onboarding_progress
from app.security.jwt_handler import verify_token

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


# -----------------------------------
# AUTH HELPER
# -----------------------------------
def get_current_user(authorization: str):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        token = authorization.split(" ")[1]
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid auth header")

    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")

    return decoded


# -----------------------------------
# GET TOUR STATUS
# -----------------------------------
@router.get("/status")
async def get_tour_status(authorization: str = Header(None)):
    user = get_current_user(authorization)

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
# async def update_tour_status(data: dict, authorization: str = Header(None)):
#     user = get_current_user(authorization)
#     now = datetime.utcnow()

#     await onboarding_progress.update_one(
#         {"user_id": user["sub"], "role": user["role"]},
#         {
#             "$set": {
#                 "version": data.get("version"),
#                 "has_completed": data.get("has_completed", False),
#                 "manually_started": data.get("manually_started", False),
#                 "last_run_at": now,
#                 "updated_at": now,
#             },
#             "$setOnInsert": {
#                 "created_at": now,
#             },
#         },
#         upsert=True,
#     )

#     return {"message": "Tour status updated successfully"}
@router.post("/status")
async def update_tour_status(data: dict, authorization: str = Header(None)):
    user = get_current_user(authorization)
    now = datetime.utcnow()

    update_fields = {
        "updated_at": now,
        "last_run_at": now,  # always update when tour touched
    }

    # Only update fields if provided
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
