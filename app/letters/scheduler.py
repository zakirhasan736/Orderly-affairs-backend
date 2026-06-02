# app/nok_letter/scheduler.py
from __future__ import annotations
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone, timedelta
from typing import Optional
from bson import ObjectId

from app.database import db
from app.config import settings
from .email_utils import render_email_html, send_email

scheduled_letters = db["scheduled_letters"]
nok_letters = db["nok_letters"]

def _backoff():
    # Use settings.SEND_RETRY_BACKOFF if present, else 10 minutes
    return getattr(settings, "SEND_RETRY_BACKOFF", timedelta(minutes=10))

async def _process_due(limit: int = 50):
    now = datetime.now(timezone.utc)

    cursor = scheduled_letters.find({
        "status": "scheduled",
        "send_at": {"$lte": now}
    }).limit(limit)

    async for job in cursor:
        claimed = await scheduled_letters.update_one(
            {"_id": job["_id"], "status": "scheduled"},
            {"$set": {"status": "processing", "updated_at": now}}
        )
        if getattr(claimed, "modified_count", 0) != 1:
            continue

        try:
            job = await scheduled_letters.find_one({"_id": job["_id"]})
            letter_id = job.get("letter_id")
            letter = await nok_letters.find_one({"_id": ObjectId(letter_id)}) if letter_id else None
            if not letter:
                raise RuntimeError("Letter not found")
            if letter.get("delivery_status") == "sent":
                await scheduled_letters.update_one(
                    {"_id": job["_id"]},
                    {"$set": {"status": "cancelled", "updated_at": now}}
                )
                continue

            to_email = letter.get("nok_email")
            if not to_email:
                raise RuntimeError("NOK email missing")

            subject = job.get("subject") or "Letter to Next of Kin"
            html = render_email_html(letter)
            await send_email(to_email, subject, html)

            await scheduled_letters.update_one(
                {"_id": job["_id"]},
                {"$set": {"status": "sent", "sent_at": now, "updated_at": now}}
            )
            await nok_letters.update_one(
                {"_id": letter["_id"]},
                {"$set": {"delivery_status": "sent", "sent_at": now, "updated_at": now}}
            )
        except Exception as e:
            attempts = int(job.get("attempts", 0)) + 1
            await scheduled_letters.update_one(
                {"_id": job["_id"]},
                {"$set": {
                    "status": "scheduled",
                    "last_error": str(e),
                    "attempts": attempts,
                    "updated_at": now,
                    "send_at": now + _backoff()
                }}
            )

def start_scheduler():
    sch = AsyncIOScheduler(timezone="UTC")
    sch.add_job(_process_due, "interval", seconds=60, max_instances=1, coalesce=True)
    sch.start()
