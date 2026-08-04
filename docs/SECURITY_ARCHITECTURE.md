# Orderly Affairs — Security Architecture

Design reference (Aug 4, 2026). Not an attacker report.

Interactive canvas (diagrams + charts): open via **File → Open File**:

`C:\Users\zakir\.cursor\projects\c-Users-zakir-Desktop-deityvillas-mobile-app-1\canvases\orderly-security-architecture.canvas.tsx`

---

## 1. System topology

```
Owner ──┐
NOK   ──┼──► Next.js Portal ──► FastAPI ──┬──► MongoDB (AES-GCM ciphertext)
Admin ──┘     CSP · CSRF · idle              ├──► Cloudinary (authenticated + signed)
                                              └──► Stripe / SendGrid / Twilio / Turnstile
```

### Cookie namespaces (isolated)

| Portal | Cookies | Access TTL |
|--------|---------|------------|
| Owner | `auth_token` + `oa_refresh_token` | 15 min |
| NOK | `nok_auth_token` + `oa_nok_refresh_token` | Full Kit **5 min** · section **10 min** |
| Admin | `oa_admin_auth_token` + `oa_admin_refresh_token` | MFA required |

Flags: HttpOnly · SameSite=Lax · Secure (non-dev) · shared Domain in production  
CSRF: `oa_csrf_token` + `X-CSRF-Token` on mutating cookie requests

---

## 2. Auth flow

1. Credentials + CAPTCHA (Turnstile)  
2. Rate limit (10 / 15 min)  
3. MFA / OTP (email · TOTP · SMS)  
4. Issue JWT RS256 into HttpOnly cookies  
5. Set CSRF token (cookie + response header)

| Role | MFA | Session |
|------|-----|---------|
| Owner | When enrolled | 15m access · 7d refresh (hashed, rotated) |
| NOK | Email OTP forced if none enrolled | 5m / 10m |
| Admin | Authenticator required | Dedicated cookies · no owner fallback in prod |

---

## 3. Vault data flow

```
Form/upload → Auth+CSRF → AES-256-GCM (+ AAD) → Mongo blob
                        ↘ authenticated Cloudinary → signed URL on read
```

AAD examples: `section:owner:id` · `message:owner` · `ai_extract:user:file` · `totp:email`

---

## 4. Security check by area

| Area | Controls | Status |
|------|----------|--------|
| Session transport | HttpOnly cookies · SameSite · Secure · Domain | Active |
| Session crypto | JWT RS256 · dual public key · hashed refresh | Active |
| CSRF | Double-submit · webhook exempt | Active |
| Owner MFA | Email / TOTP / SMS when enrolled | Active |
| NOK MFA | Email OTP forced if none enrolled | Active |
| Admin MFA | Authenticator · isolated cookies | Active |
| Bot / abuse | Turnstile · auth rate limit · API throttle | Active |
| Vault confidentiality | AES-256-GCM + AAD | Active |
| AI OCR text | AES-GCM AAD | Active |
| TOTP secrets | AES-GCM at rest | Active |
| File / media | Authenticated CDN · signed URLs · fail-closed | Active |
| Portal XSS | CSP nonce + strict-dynamic · escaped print | Active |
| API hardening | HSTS · nosniff · frame DENY · docs off in prod | Active |
| Payments | Stripe webhook signature | Active |
| Key rotation | Dual-key + scripts | Ready |

---

## 5. Keys & secrets

| Name | Type | Protects | Rotation |
|------|------|----------|----------|
| JWT private/public | RS256 | Access / MFA challenge JWTs | 90d · `generate_jwt_keys.py` |
| `AES_256_KEY` | AES-256-GCM | Vault, messages, TOTP, AI OCR | 365d · `rotate_aes_key.py` |
| Refresh tokens | Opaque · SHA-256 stored | Silent renew | Rotate on use · 7d |
| `oa_csrf_token` | Random | CSRF binding | Per response |
| Cloudinary / Stripe / Turnstile / SendGrid / Twilio / Mongo | Provider secrets | Integrations | Provider dashboards |

---

## 6. Intentional bypass zones

| Zone | Skipped | Why | Compensating |
|------|---------|-----|--------------|
| `/billing/webhook` | CSRF | Stripe server callback | Signature verify |
| Login before session | CSRF | No cookie yet | CAPTCHA + rate limit |
| Bearer-only clients | CSRF | Not cookie auth | Authorization header |
| `APP_ENV=development` | HSTS / some limits / CAPTCHA bypass | Local DX | Never on public hosts |
| Admin owner-cookie fallback | Isolation | Dev only by default | Off in production |
| CSP `style-src unsafe-inline` | Strict styles | Next.js CSS | Script nonce remains |
| NOK create password once | List omit | Print card | Reveal rate-limited |

---

## 7. Who can reach what

| Surface | Owner | NOK | Admin |
|---------|-------|-----|-------|
| Sections 1–21 | Full R/W | Authorized only | Ops APIs |
| Messages / letters | Compose | Receive | — |
| NOK password reveal | Rate-limited | Own login | — |
| Billing | Stripe | Blocked if owner blocked | Overview |
| `/admin/*` | No (prod) | No | MFA cookie |
| AI docs | Own | No | — |

---

## 8. Verify

```bash
python scripts/verify_bank_grade.py
```

See also: `PRODUCTION_SETUP.md` · `app/security/KEY_ROTATION.md`
