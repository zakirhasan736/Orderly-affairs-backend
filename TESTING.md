# Automated testing

## Backend (pytest)

```bash
cd Orderly-affairs-backend-1
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

### Coverage map

| Area | File |
|------|------|
| Access Management / NOK validation + section ACL | `tests/test_access_management.py` |
| Empty sections + captcha gates | `tests/test_section_and_captcha.py` |
| OTP send lock (no duplicate SMS/email) | `tests/test_otp_send_lock.py` |
| Auth phone, OTP hash storage, passwords, rate-limit steps, error sanitization | `tests/test_auth_security.py` |
| Section 6 upload coerce, media/file validation, NOK cross-access, SMS country allowlist | `tests/test_sections_media_nok.py` |
| Middleware / routing / JWT / cookies / HTTPS / security headers / crypto / billing / usage | `tests/test_middleware_routes_security.py` |

`tests/conftest.py` sets AES/JWT/Mongo env before imports.

### Middleware & security suite covers

- Security headers (`nosniff`, `DENY`, CSP, HSTS outside development)
- HTTPS redirect on `x-forwarded-proto: http` in production
- Production-masked 404 / 422 / 500 responses
- Bearer + HTTP-only cookie JWT auth roundtrips (HS256 patched)
- Role gate (nextkin blocked from owner-only)
- Section AES encrypt/decrypt + wrong-context fail-closed
- Billing vault lock + nextkin usage limits

## Frontend (Vitest)

```bash
cd Orderly-affairs-frontend-1
npm install --legacy-peer-deps
npm test
```

See frontend `TESTING.md` (logic + RTL UI flows).

## Suggested habit

```bash
# backend
python -m pytest -q
# frontend
npm test
```

## Scope note

Suites target **auth, Access Management, AI autofill, uploads, NOK, middleware, and HTTP security** — the highest-risk portal paths. Full browser E2E across every section UI is a separate Playwright track if you want that next.
