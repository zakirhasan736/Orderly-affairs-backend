from fastapi import APIRouter, Header, HTTPException
from app.database import users_collection
from app.security.jwt_handler import verify_token

admin_billing_router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


def require_admin(authorization: str):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return decoded


@admin_billing_router.get("/overview")
async def billing_overview(authorization: str = Header(...)):
    require_admin(authorization)

    pipeline = [
        {"$group": {
            "_id": "$billing.status",
            "count": {"$sum": 1}
        }}
    ]

    stats = await users_collection.aggregate(pipeline).to_list(None)
    return {"stats": stats}


@admin_billing_router.get("/users")
async def billing_users(authorization: str = Header(...)):
    require_admin(authorization)

    cursor = users_collection.find(
        {"role": "owner"},
        {
            "email": 1,
            "billing": 1,
            "created_at": 1,
        }
    )

    users = []
    async for u in cursor:
        users.append({
            "email": u["email"],
            "status": u["billing"]["status"],
            "plan": u["billing"].get("plan"),
            "subscription_id": u["billing"].get("subscription_id"),
            "trial_end": u["billing"].get("trial_end"),
        })

    return users
