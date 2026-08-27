from __future__ import annotations

import random
import threading
import time
from collections import deque
from typing import Any

import requests

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


class RateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self._lock = threading.Lock()
        self._hits: deque[float] = deque()

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - 60.0
                while self._hits and self._hits[0] < cutoff:
                    self._hits.popleft()
                if len(self._hits) < self.max_per_minute:
                    self._hits.append(now)
                    return
                sleep_for = max(0.05, 60.0 - (now - self._hits[0]))
            time.sleep(sleep_for)


class ReadOnlyHttpClient:
    """GET/HEAD only. Never sends POST/PUT/PATCH/DELETE."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float,
        rate_limiter: RateLimiter,
        max_attempts: int = 5,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, */*;q=0.8",
                "User-Agent": user_agent,
            }
        )
        self.timeout = timeout
        self.rate_limiter = rate_limiter
        self.max_attempts = max_attempts

    def close(self) -> None:
        self.session.close()

    def get(
        self,
        url: str,
        *,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> tuple[requests.Response, list[dict[str, Any]]]:
        return self._request("GET", url, stream=stream, headers=headers, allow_redirects=allow_redirects)

    def head(self, url: str, *, headers: dict[str, str] | None = None) -> tuple[requests.Response | None, list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        try:
            response, attempts = self._request("HEAD", url, stream=False, headers=headers, allow_redirects=True)
            return response, attempts
        except Exception as exc:  # noqa: BLE001 — HEAD is optional
            attempts.append({"method": "HEAD", "error": str(exc)})
            return None, attempts

    def _request(
        self,
        method: str,
        url: str,
        *,
        stream: bool,
        headers: dict[str, str] | None,
        allow_redirects: bool,
    ) -> tuple[requests.Response, list[dict[str, Any]]]:
        method = method.upper()
        if method not in {"GET", "HEAD"}:
            raise RuntimeError(f"Read-only client refused method {method}")
        attempts: list[dict[str, Any]] = []
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.rate_limiter.wait()
            started = time.time()
            record: dict[str, Any] = {
                "attempt": attempt,
                "method": method,
                "url": url,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.timeout,
                    stream=stream,
                    headers=headers,
                    allow_redirects=allow_redirects,
                )
                record["http_status"] = response.status_code
                record["elapsed_ms"] = int((time.time() - started) * 1000)
                record["content_type"] = response.headers.get("Content-Type")
                attempts.append(record)
                if response.status_code in RETRYABLE_STATUS and attempt < self.max_attempts:
                    self._sleep_backoff(attempt, response.headers.get("Retry-After"))
                    continue
                return response, attempts
            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                record["error"] = str(exc)
                attempts.append(record)
                if attempt < self.max_attempts:
                    self._sleep_backoff(attempt, None)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Request failed without response: {url}")

    @staticmethod
    def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                wait = min(30.0, max(0.2, float(retry_after)))
                time.sleep(wait + random.random() * 0.25)
                return
            except ValueError:
                pass
        base = min(16.0, 0.5 * (2 ** (attempt - 1)))
        time.sleep(base + random.random() * 0.35)
