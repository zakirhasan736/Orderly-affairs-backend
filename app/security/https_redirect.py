"""Redirect HTTP to HTTPS in production (behind reverse proxy)."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import settings


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.is_development:
            forwarded_proto = request.headers.get("x-forwarded-proto", "")
            if forwarded_proto and forwarded_proto.lower() != "https":
                url = request.url.replace(scheme="https")
                return RedirectResponse(url=str(url), status_code=301)

        return await call_next(request)
