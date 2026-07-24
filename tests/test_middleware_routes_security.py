"""Backend integration-style unit tests — middleware, routing, auth security."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.billing.access import (
    PAYMENT_LOCK_MESSAGE,
    enforce_vault_access,
    is_billing_only,
    requires_billing_setup,
)
from app.security.cookie_auth import (
    extract_access_token,
    extract_bearer_from_header,
)
from app.security.crypto import is_encrypted_payload
from app.security.error_handlers import (
    GENERIC_NOT_FOUND,
    GENERIC_SERVER_ERROR,
    GENERIC_VALIDATION_ERROR,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.security.https_redirect import HTTPSRedirectMiddleware
from app.security.jwt_handler import (
    create_access_token,
    create_mfa_challenge_token,
    verify_mfa_challenge_token,
    verify_token,
)
from app.security.section_crypto import decrypt_section_data, encrypt_section_data
from app.security.security_headers import SecurityHeadersMiddleware
from app.security.usage_guard import enforce_usage


@pytest.fixture
def security_client():
    app = FastAPI()

    @app.get("/secure-data")
    async def secure_data(request: Request):
        token = extract_access_token(
            request,
            request.headers.get("authorization"),
            required=True,
        )
        payload = verify_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid token")
        return {"sub": payload["sub"], "role": payload["role"]}

    @app.get("/owner-only")
    async def owner_only(request: Request):
        token = extract_access_token(
            request,
            request.headers.get("authorization"),
            required=True,
        )
        payload = verify_token(token)
        if not payload or payload.get("role") != "owner":
            raise HTTPException(status_code=403, detail="Owners only")
        return {"allowed": True}

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(SecurityHeadersMiddleware)

    with patch("app.security.security_headers.settings") as settings:
        settings.APP_ENV = "development"
        yield TestClient(app)


class TestSecurityHeadersMiddleware:
    def test_sets_security_headers_on_responses(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        app = FastAPI()

        @app.get("/ping")
        async def ping():
            return {"pong": True}

        app.add_middleware(SecurityHeadersMiddleware)
        with patch("app.security.security_headers.settings") as settings:
            settings.APP_ENV = "development"
            client = TestClient(app)
            res = client.get("/ping")

        assert res.status_code == 200
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]
        assert res.headers["Cache-Control"].startswith("no-store")
        assert "Strict-Transport-Security" not in res.headers

    def test_adds_hsts_outside_development(self):
        app = FastAPI()

        @app.get("/ping")
        async def ping():
            return {"pong": True}

        app.add_middleware(SecurityHeadersMiddleware)
        with patch("app.security.security_headers.settings") as settings:
            settings.APP_ENV = "production"
            client = TestClient(app)
            res = client.get("/ping")

        assert "max-age=" in res.headers["Strict-Transport-Security"]


class TestHttpsRedirectMiddleware:
    def test_redirects_http_forwarded_proto_in_production(self):
        app = FastAPI()

        @app.get("/vault")
        async def vault():
            return {"ok": True}

        app.add_middleware(HTTPSRedirectMiddleware)
        with patch("app.security.https_redirect.settings") as settings:
            settings.APP_ENV = "production"
            client = TestClient(app, follow_redirects=False)
            res = client.get(
                "/vault",
                headers={"x-forwarded-proto": "http"},
            )

        assert res.status_code == 301
        assert res.headers["location"].startswith("https://")

    def test_allows_http_in_development(self):
        app = FastAPI()

        @app.get("/vault")
        async def vault():
            return {"ok": True}

        app.add_middleware(HTTPSRedirectMiddleware)
        with patch("app.security.https_redirect.settings") as settings:
            settings.APP_ENV = "development"
            client = TestClient(app)
            res = client.get(
                "/vault",
                headers={"x-forwarded-proto": "http"},
            )

        assert res.status_code == 200


class TestErrorHandlingRoutes:
    def test_http_exception_and_validation_shapes(self):
        from pydantic import BaseModel, EmailStr

        class BodyIn(BaseModel):
            email: EmailStr

        app = FastAPI()

        @app.get("/missing")
        async def missing():
            raise HTTPException(status_code=404, detail="gone")

        @app.post("/body")
        async def body(payload: BodyIn):
            return payload

        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_exception_handler)

        with patch("app.security.error_handlers._is_production", return_value=True):
            client = TestClient(app, raise_server_exceptions=False)
            missing_res = client.get("/missing")
            assert missing_res.status_code == 404
            assert missing_res.json()["detail"] == GENERIC_NOT_FOUND

            bad = client.post("/body", json={})
            assert bad.status_code == 422
            assert bad.json()["detail"] == GENERIC_VALIDATION_ERROR

    def test_unhandled_exception_masked_in_production(self):
        app = FastAPI()

        @app.get("/crash")
        async def crash():
            raise RuntimeError("internal")

        app.add_exception_handler(Exception, unhandled_exception_handler)

        with patch("app.security.error_handlers._is_production", return_value=True):
            client = TestClient(app, raise_server_exceptions=False)
            res = client.get("/crash")

        assert res.status_code == 500
        assert res.json()["detail"] == GENERIC_SERVER_ERROR


class TestJwtAndCookieAuth:
    def test_bearer_extraction(self):
        assert extract_bearer_from_header(None) is None
        assert extract_bearer_from_header("Token abc") is None
        assert extract_bearer_from_header("Bearer  ") is None
        assert extract_bearer_from_header("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_access_token_roundtrip_owner(self):
        with patch("app.security.jwt_handler.settings") as jwt_settings:
            jwt_settings.JWT_ALGORITHM = "HS256"
            jwt_settings.JWT_PRIVATE_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.JWT_PUBLIC_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 15
            token = create_access_token(
                {"email": "owner@example.com", "role": "owner", "_id": "abc"}
            )
            payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "owner@example.com"
        assert payload["role"] == "owner"

    def test_mfa_challenge_token_binds_email(self):
        with patch("app.security.jwt_handler.settings") as jwt_settings:
            jwt_settings.JWT_ALGORITHM = "HS256"
            jwt_settings.JWT_PRIVATE_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.JWT_PUBLIC_KEY = "orderly-test-jwt-secret-key"
            token = create_mfa_challenge_token("Owner@Example.com")
            ok = verify_mfa_challenge_token(token, "owner@example.com")
            bad = verify_mfa_challenge_token(token, "other@example.com")
            missing = verify_mfa_challenge_token(None, "owner@example.com")
        assert ok is True
        assert bad is False
        assert missing is False

    def test_secure_route_accepts_bearer_and_rejects_bad_token(self, security_client):
        with patch("app.security.jwt_handler.settings") as jwt_settings:
            jwt_settings.JWT_ALGORITHM = "HS256"
            jwt_settings.JWT_PRIVATE_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.JWT_PUBLIC_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 15
            token = create_access_token(
                {"email": "owner@example.com", "role": "owner", "_id": "1"}
            )
            ok = security_client.get(
                "/secure-data",
                headers={"Authorization": f"Bearer {token}"},
            )
            bad = security_client.get(
                "/secure-data",
                headers={"Authorization": "Bearer not-a-token"},
            )
            missing = security_client.get("/secure-data")

            assert ok.status_code == 200
            assert ok.json()["sub"] == "owner@example.com"
            assert bad.status_code == 401
            assert missing.status_code == 401

    def test_owner_only_blocks_nextkin_role(self, security_client):
        with patch("app.security.jwt_handler.settings") as jwt_settings:
            jwt_settings.JWT_ALGORITHM = "HS256"
            jwt_settings.JWT_PRIVATE_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.JWT_PUBLIC_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 15
            nok_token = create_access_token(
                {
                    "email": "nok@example.com",
                    "role": "nextkin",
                    "_id": "nok1",
                    "owner_id": "owner1",
                }
            )
            res = security_client.get(
                "/owner-only",
                headers={"Authorization": f"Bearer {nok_token}"},
            )
            assert res.status_code == 403

    def test_secure_route_accepts_http_only_cookie_token(self, security_client):
        from app.security.cookie_auth import OWNER_ACCESS_COOKIE

        with patch("app.security.jwt_handler.settings") as jwt_settings:
            jwt_settings.JWT_ALGORITHM = "HS256"
            jwt_settings.JWT_PRIVATE_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.JWT_PUBLIC_KEY = "orderly-test-jwt-secret-key"
            jwt_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 15
            token = create_access_token(
                {"email": "cookie@example.com", "role": "owner", "_id": "c1"}
            )
            security_client.cookies.set(OWNER_ACCESS_COOKIE, token)
            res = security_client.get("/secure-data")
            assert res.status_code == 200
            assert res.json()["sub"] == "cookie@example.com"


class TestCryptoAndSectionEncryption:
    def test_encrypt_decrypt_roundtrip_with_context(self):
        payload = {"5A": [{"make": "Honda", "year": "2022"}]}
        token = encrypt_section_data("owner-1", "5", payload)
        assert is_encrypted_payload(token) is True
        assert decrypt_section_data("owner-1", "5", token) == payload

    def test_empty_section_decrypt_returns_empty(self):
        assert decrypt_section_data("owner-1", "5", "") == {}

    def test_wrong_section_context_fails_closed(self):
        token = encrypt_section_data("owner-1", "5", {"secret": True})
        with pytest.raises(Exception):
            decrypt_section_data("other-owner", "9", token)


class TestBillingAndUsageGuards:
    def test_billing_only_and_requires_setup(self):
        assert requires_billing_setup({"status": "pending"}) is True
        assert requires_billing_setup({"status": "active"}) is False
        assert is_billing_only({"status": "past_due"}) is True
        assert is_billing_only({"status": "active"}) is False

    def test_enforce_vault_access_blocks_past_due(self):
        with pytest.raises(HTTPException) as exc:
            enforce_vault_access({"billing": {"status": "past_due"}})
        assert exc.value.status_code in (402, 403)
        assert PAYMENT_LOCK_MESSAGE.split()[0] in str(exc.value.detail)

    def test_usage_limit_for_nextkin(self):
        user = {"billing": {"plan": "monthly"}}
        enforce_usage(user, "nextkin", 2)
        with pytest.raises(HTTPException) as exc:
            enforce_usage(user, "nextkin", 3)
        assert exc.value.status_code == 403
