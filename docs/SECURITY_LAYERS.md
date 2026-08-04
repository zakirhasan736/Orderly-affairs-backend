# Orderly Affairs — 8-Layer Security Model

Applies to **Owner portal**, **Next-of-Kin**, and **Admin** for authentication and personal kit data.

Date: Aug 4, 2026

---

## Cookie check (already in place)

All three portals use the **same** cookie helper: `set_auth_cookie()` in `app/security/cookie_auth.py`.

| Flag | Value | Owner | NOK | Admin |
|------|-------|-------|-----|-------|
| **HttpOnly** | `True` | Yes | Yes | Yes |
| **Secure** | On when `APP_ENV != development` | Yes | Yes | Yes |
| **SameSite** | `Lax` | Yes | Yes | Yes |
| **Path** | `/` | Yes | Yes | Yes |
| **Domain** | Shared in production (e.g. `.orderly-affairs.com`) | Yes | Yes | Yes |

| Portal | Access cookie | Refresh cookie | Issued by |
|--------|---------------|----------------|-----------|
| Owner | `auth_token` | `oa_refresh_token` | `issue_owner_session` |
| NOK | `nok_auth_token` | `oa_nok_refresh_token` | `issue_nok_session` |
| Admin | `oa_admin_auth_token` | `oa_admin_refresh_token` | `issue_admin_session` |

Namespaces are **isolated** (logging into one role clears the others’ cookies where session issue logic requires it).

**Note:** `oa_csrf_token` is intentionally **not** HttpOnly so the portal can send `X-CSRF-Token`. Auth JWTs never go in `localStorage`.

---

## The 8 layers (defense in depth)

```
┌─────────────────────────────────────────────────────────────┐
│ 8. Portal hardening     CSP nonce · idle logout · no JWT LS │
├─────────────────────────────────────────────────────────────┤
│ 7. Media / files        Authenticated Cloudinary · signed   │
├─────────────────────────────────────────────────────────────┤
│ 6. Encryption at rest   AES-256-GCM + AAD (personal vault)  │
├─────────────────────────────────────────────────────────────┤
│ 5. Authorization        Owner / NOK ACL / Admin no-vault    │
├─────────────────────────────────────────────────────────────┤
│ 4. Step-up identity     MFA · email OTP · TOTP · SMS        │
├─────────────────────────────────────────────────────────────┤
│ 3. Login abuse controls CAPTCHA · rate limits · lock windows│
├─────────────────────────────────────────────────────────────┤
│ 2. Session cookies      HttpOnly · Secure · SameSite · CSRF │
├─────────────────────────────────────────────────────────────┤
│ 1. Transport            HTTPS / TLS · HSTS (production)     │
└─────────────────────────────────────────────────────────────┘
                         ▲
              Personal data + auth traffic
```

### Layer 1 — Transport
- HTTPS redirect + HSTS when not development  
- Protects all Owner / NOK / Admin traffic in transit  

### Layer 2 — Session cookies + CSRF
- **HttpOnly** JWT cookies for Owner, NOK, **and** Admin (verified above)  
- Refresh tokens hashed in Mongo, rotated on use  
- CSRF double-submit on mutating cookie requests  
- Short access TTLs: Owner 15m · NOK Full Kit 5m · NOK section 10m  

### Layer 3 — Login abuse controls
- Cloudflare Turnstile CAPTCHA  
- Auth rate limit (default 10 / 15 min)  
- OTP send/verify locks  

### Layer 4 — Step-up identity (MFA)
- Owner: MFA when enrolled  
- NOK: email OTP forced if no method enrolled  
- Admin: authenticator MFA required  

### Layer 5 — Authorization (who may see personal data)
- Owner: full kit R/W  
- NOK: only owner-granted Full Kit or `authorized_sections` · read-only  
- Admin: account/billing metadata — **no vault content decrypt APIs**  

### Layer 6 — Database / personal information encryption
- **E2EE v3 (preferred):** client AES-GCM; server stores opaque ciphertext only — see `docs/E2EE.md`  
- **Legacy v2:** Sections, messages, letters, NOK profile secrets, AI OCR, TOTP → **AES-256-GCM** with server `AES_256_KEY`  
- AAD binds v2 ciphertext to record scope  
- Server decrypts v2 only after Layers 2–5 succeed; **never decrypts v3** 

### Layer 7 — Files & media
- Authenticated Cloudinary delivery by default  
- Short-lived signed URLs; fail-closed if signing fails  

### Layer 8 — Portal / browser hardening
- CSP with per-request script nonce + `strict-dynamic`  
- Idle session guards (Owner + NOK pages)  
- Letter print HTML escaped  
- No access JWT in `localStorage`  

---

## Mapping layers → areas you asked about

| Your area | Layers |
|-----------|--------|
| **Auth** (Owner / NOK / Admin) | 1, 2, 3, 4, 8 |
| **HttpOnly cookies** | 2 (all three portals — confirmed) |
| **User personal information** | 5, 6 (who + encrypt) |
| **Database** | 6 (+ 1 for wire, 5 for who decrypts) |
| **Portal already checked** | 2, 8 (+ CSRF client in `secureFetch` / RTK) |

---

## How to describe this publicly (accurate)

> Orderly Affairs uses an eight-layer defense-in-depth model: TLS in transit; HttpOnly Secure cookies for Owner, Next-of-Kin, and Admin sessions with CSRF protection; CAPTCHA and rate limiting; multi-factor / one-time codes; role-based access so Next-of-Kin only see Owner-granted areas and admins cannot open vault contents; AES-256-GCM encryption of personal kit data at rest; authenticated file storage with signed links; and portal CSP plus idle session controls.

**Do not** describe messages/AI OCR as E2EE until those paths are migrated — vault **sections** at v3 are E2EE.

---

## Quick verification

```bash
# Cookie helper always sets httponly=True
rg -n "httponly" app/security/cookie_auth.py

# All three session issuers use set_auth_cookie
rg -n "issue_owner_session|issue_nok_session|issue_admin_session" app/auth/session_manager.py

python scripts/verify_bank_grade.py
```

Related: `docs/PERSONAL_DATA_SECURITY_CLAIMS.md` · `docs/SECURITY_ARCHITECTURE.md`
