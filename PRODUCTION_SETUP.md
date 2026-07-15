# Production setup guide

This document explains the security-related environment variables and what to change when moving Orderly Affairs from local development to production.

## Cloudflare Turnstile (OTP CAPTCHA)

Turnstile protects OTP send/verify flows from bots. You need **two** keys — one for the backend, one for the frontend.

| Variable | Where | Purpose |
|----------|-------|---------|
| `TURNSTILE_SECRET_KEY` | Backend `.env` | Server-side token verification |
| `NEXT_PUBLIC_TURNSTILE_SITE_KEY` | Frontend `.env` | Widget shown to users in the browser |

### How to get keys

1. Sign in to the [Cloudflare dashboard](https://dash.cloudflare.com/).
2. Go to **Turnstile** and create a widget for your production domain (e.g. `portal.orderly-affairs.com`).
3. Copy the **Site key** → `NEXT_PUBLIC_TURNSTILE_SITE_KEY` on the frontend.
4. Copy the **Secret key** → `TURNSTILE_SECRET_KEY` on the backend.
5. Set `OTP_CAPTCHA_ENABLED=true` on the backend (default).

In development, Turnstile can be left empty: the frontend bypasses the widget and the backend can skip verification when no secret is configured.

### Cookie domain (portal + API subdomains)

Auth cookies are set by `api.orderly-affairs.com`. The portal middleware also reads `auth_token`. In production the API automatically sets `Domain=.orderly-affairs.com` (derived from `FRONTEND_URL`) so cookies are shared across subdomains.

Optional override in backend `.env`:

```env
COOKIE_DOMAIN=.orderly-affairs.com
FRONTEND_URL=https://portal.orderly-affairs.com
```

If this is wrong, verify-email / login succeeds but `/dashboard` immediately redirects back to login.


If the portal shows `/cdn-cgi/challenge-platform` 404s and auth CAPTCHA calls return 400, temporarily disable captcha on **both** sides, redeploy, then re-enable after fixing Cloudflare Turnstile keys/hostnames:

| Variable | Backend | Frontend (rebuild required) |
|----------|---------|-------------------------------|
| `OTP_CAPTCHA_ENABLED=false` | `.env` then `pm2 restart` | — |
| `NEXT_PUBLIC_OTP_CAPTCHA_ENABLED=false` | — | `.env` then rebuild/redeploy portal |

Re-enable both to `true` once Turnstile siteverify succeeds.

---

## `APP_ENV=production` and HTTPS

Set `APP_ENV=production` on the backend when deployed. This enables:

- **Secure cookies** — auth tokens are set with `Secure` and `SameSite=strict` (see `app/security/cookie_auth.py`). Browsers only send them over HTTPS.
- **HSTS** — `Strict-Transport-Security` is added to API responses (see `app/security/security_headers.py`).
- **HTTP → HTTPS redirect** — `HTTPSRedirectMiddleware` redirects requests when `X-Forwarded-Proto` is not `https` (typical behind nginx, Cloudflare, or a load balancer).

Keep `APP_ENV=development` locally so cookies work on `http://localhost` and redirects are not applied.

Your reverse proxy must forward `X-Forwarded-Proto: https` to the API.

---

## `ENABLE_EDGE_SESSION_CHECK` (frontend)

The Next.js middleware (`src/middleware.ts`) checks for auth cookies on protected routes. By default it only checks **presence** of a cookie — fast, but a stale or revoked token could still pass until the next API call.

Set `ENABLE_EDGE_SESSION_CHECK=true` in production to also call `GET /auth/session` on each protected navigation. Invalid sessions are cleared and the user is redirected to login.

Trade-off: one extra backend request per protected page load. Recommended for production; keep `false` locally to reduce latency during development.

---

## Local vs production checklist

### Local development

| Setting | Backend | Frontend |
|---------|---------|----------|
| `APP_ENV` | `development` | — |
| `FRONTEND_URL` | `http://localhost:3000` | — |
| `NEXT_PUBLIC_API_BASE_URL` | — | `http://localhost:8000` |
| Turnstile keys | empty (optional) | empty (optional) |
| `OTP_CAPTCHA_ENABLED` | `true` or `false` | — |
| `ENABLE_EDGE_SESSION_CHECK` | — | `false` |
| Stripe / Twilio | test credentials | test publishable key |

### Production

| Setting | Backend | Frontend |
|---------|---------|----------|
| `APP_ENV` | `production` | — |
| `FRONTEND_URL` | `https://portal.orderly-affairs.com` (your domain) | — |
| `NEXT_PUBLIC_API_BASE_URL` | — | `https://api.orderly-affairs.com` (your API URL) |
| Turnstile keys | `TURNSTILE_SECRET_KEY` set | `NEXT_PUBLIC_TURNSTILE_SITE_KEY` set |
| `OTP_CAPTCHA_ENABLED` | `true` | — |
| `ENABLE_EDGE_SESSION_CHECK` | — | `true` |
| Stripe / Twilio | live credentials | live publishable key |
| TLS | HTTPS with `X-Forwarded-Proto` | HTTPS site |

### Before go-live

- [ ] Copy `.env.example` → `.env` on both repos; fill all required values.
- [ ] `APP_ENV=production` on backend.
- [ ] `FRONTEND_URL` matches the real frontend origin (used for CORS and email links).
- [ ] Turnstile widget domain matches production hostname.
- [ ] `ENABLE_EDGE_SESSION_CHECK=true` on frontend.
- [ ] Stripe webhook URL and `STRIPE_WEBHOOK_SECRET` point to production.
- [ ] Rotate `AES_256_KEY` and JWT keys per `app/security/KEY_ROTATION.md` if migrating from dev.
- [ ] Run TOTP encryption migration (see below).
- [ ] Run `python scripts/security_smoke_test.py` against production API (all checks pass).

---

## TOTP secrets at rest

Authenticator MFA secrets (`totp_secret`, `provisioned_secret`) are encrypted with AES-256-GCM and context-bound to the user email (same key as vault data: `AES_256_KEY`).

### One-time migration

If the database was created before this encryption shipped, run:

```bash
cd Orderly-affairs-backend-1
python scripts/migrate_totp_secrets.py
```

This encrypts any plaintext TOTP values in `users` and `pending_signups`. The script prints before/after counts and exits non-zero if plaintext remains.

On every API startup, `run_encryption_migration()` also includes a `totp_secrets` step so new deploys self-heal without a manual run.

### Verify

```bash
python -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.security.security_audit import audit_totp_secrets
print(asyncio.run(audit_totp_secrets()))
"
```

Expect `plain_users: 0` and `plain_pending: 0`.

---

## Bank-grade security checklist

Use this before calling the platform production-ready.

| Control | Requirement | Verified by |
|---------|-------------|-------------|
| Auth transport | HttpOnly cookies; no JWT in `localStorage` | Frontend `secureFetch`, middleware |
| Session refresh | Cookie-only rotation; no Bearer refresh fallback | Smoke test #13 |
| MFA enrollment | QR only; no `secret` in API JSON | Signup / `generate-mfa` / `resume-pending-signup` |
| MFA at rest | Encrypted `totp_secret` | `migrate_totp_secrets.py` + audit |
| Brute force | Turnstile on owner login, NOK login, OTP sends | Smoke tests #3–6, #9–11 |
| Rate limits | Active when `APP_ENV=production` | `auth_rate_limit.py`, `api_rate_limit.py` |
| File delete IDOR | `orderly_affairs/{owner_email}/` prefix on all deletes | Section routers + `/uploads/delete` |
| Vault encryption | AES-256-GCM section/message/kit data | Startup migration + security audit |
| Error hygiene | Generic messages on auth/MFA; `getSafeErrorMessage` on frontend | Smoke tests, UI review |
| HTTPS | `APP_ENV=production`, HSTS, secure cookies | `PRODUCTION_SETUP` TLS section |
| Edge sessions | `ENABLE_EDGE_SESSION_CHECK=true` | Frontend middleware |
| Dead auth code | No client-side password checks | Repo grep / deleted legacy components |

### Upon-death confirmation (bank-grade)

- Checklist items **no longer** auto-mark an owner deceased. They set `death_signals_pending_confirmation` when enough signals are checked.
- NOK must use **Report passing** (`POST /auth/nextkin/report-owner-deceased`) with master password + explicit confirm.
- Death-triggered messages cannot be delivered via `/kit/deliver/{id}` until `owner_status === "deceased"`.
- Inactivity scheduler sends warnings only; it **does not** auto-mark owners deceased after the grace period.


```bash
# Backend must be running
python scripts/security_smoke_test.py

# Optional full owner session test
set OA_TEST_EMAIL=you@example.com
set OA_TEST_PASSWORD=your-password
python scripts/security_smoke_test.py
```

All automated checks should pass before go-live.
