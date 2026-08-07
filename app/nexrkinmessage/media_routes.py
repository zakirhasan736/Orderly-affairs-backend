"""Deprecated duplicate of POST /message/media.

Personal-message audio/video/image uploads live on
`app.nexrkinmessage.routes.upload_message_media` (S3).
This module is intentionally not registered in `app.main`.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/message/media", tags=["Message Media"])


@router.post("")
async def upload_letter_media_deprecated():
    raise HTTPException(
        status_code=410,
        detail="Use POST /message/media — media is stored on S3.",
    )
