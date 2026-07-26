"""Production-safe API error responses (no stack traces / field dumps)."""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

GENERIC_SERVER_ERROR = "An error occurred. Please try again."
GENERIC_VALIDATION_ERROR = "Invalid request."
GENERIC_NOT_FOUND = "Not found."


def _is_production() -> bool:
    return settings.APP_ENV == "production"


def _sanitize_http_detail(status_code: int, detail) -> str | dict:
    if not _is_production():
        return detail

    if status_code >= 500:
        return GENERIC_SERVER_ERROR

    # Keep machine-readable codes for client UX (e.g. AI re-upload prompts).
    if isinstance(detail, dict) and detail.get("code"):
        return {
            "code": str(detail["code"]),
            "message": str(
                detail.get("message")
                or (
                    GENERIC_NOT_FOUND
                    if status_code == 404
                    else GENERIC_SERVER_ERROR
                )
            ),
        }

    if status_code == 404:
        return GENERIC_NOT_FOUND

    if isinstance(detail, str):
        return detail

    if isinstance(detail, list):
        return GENERIC_VALIDATION_ERROR

    if isinstance(detail, dict):
        return {"message": GENERIC_VALIDATION_ERROR}

    return GENERIC_SERVER_ERROR


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _sanitize_http_detail(exc.status_code, exc.detail)},
        headers=getattr(exc, "headers", None) or None,
    )


async def starlette_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _sanitize_http_detail(exc.status_code, exc.detail)},
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    if _is_production():
        return JSONResponse(
            status_code=422,
            content={"detail": GENERIC_VALIDATION_ERROR},
        )

    return JSONResponse(status_code=422, content={"detail": exc.errors()})


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    if _is_production():
        return JSONResponse(
            status_code=500,
            content={"detail": GENERIC_SERVER_ERROR},
        )

    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__},
    )
