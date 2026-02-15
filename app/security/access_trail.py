from pendulum import datetime


async def log_access(
    *,
    user_id: str,
    role: str,
    endpoint: str,
    ip: str | None,
    success: bool,
):
    from app.database import access_logs_collection
    await access_logs_collection.insert_one({
        "user_id": user_id,
        "role": role,
        "endpoint": endpoint,
        "ip": ip,
        "success": success,
        "timestamp": datetime.utcnow(),
    })
