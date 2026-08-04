# Personal Data Access, Risk & Security Claims

Orderly Affairs — Owner portal · Next-of-Kin · Admin  
Date: Aug 4, 2026

**Pillars (public order):** [`docs/SECURITY_PILLARS.md`](./SECURITY_PILLARS.md)  
**Canvas (diagrams):** File → Open File →  
`C:\Users\zakir\.cursor\projects\c-Users-zakir-Desktop-deityvillas-mobile-app-1\canvases\orderly-personal-data-security.canvas.tsx`

---

## Start here — four pillars (approved)

1. **Weekly security monitoring and logging** — automated encryption integrity audit + admin alerts / audit log.  
2. **Eight-layer security** for user / section data — see [`SECURITY_LAYERS.md`](./SECURITY_LAYERS.md).  
3. **End-to-end encryption for vault sections (v3)** — browser holds DEK; server/admin cannot decrypt. Legacy v2 migrates on save. See [`E2EE.md`](./E2EE.md).  
4. **HTTPS everywhere** · **AES-256 at rest** · **TLS in transit** (prefer TLS 1.3 at the edge).

Safe Trust bullets:

- ✅ Weekly security monitoring and logging  
- ✅ Eight-layer defense for personal kit / section data  
- ✅ End-to-end encryption for vault sections — platform cannot decrypt ciphertext  
- ✅ HTTPS everywhere · TLS in transit  

**Do not over-claim:** E2EE applies to vault sections at `encryption_version: 3` (re-save to migrate).

---

## 1. Who can see personal / important information?

| Data | Owner | NOK Full Kit | NOK limited (owner-granted sections) | Admin |
|------|-------|--------------|--------------------------------------|-------|
| Vault sections (bank, health, passwords, estate, etc.) | Full R/W | View all kit sections | **Only** sections in `authorized_sections` | **Cannot** open contents |
| Messages / media | Yes | Via delivery / kit path | Same if granted | No |
| Personal letter to NOK | Compose | Own letter | Own letter | No |
| NOK password card | Create + rate-limited reveal | Own login only | Own login only | No plaintext in lists |
| AI OCR text | Yes | No | No | No |
| Billing | Own | Blocked if owner blocked | Same | Metadata / ops only |
| TOTP secrets | Server verify only | Server verify only | Same | Own admin TOTP only |

### How NOK gets access

1. Owner assigns NOK and sets **Full Kit** or **section/area list**.
2. Owner enables `immediate_access`.
3. NOK logs in with password card + CAPTCHA + **email OTP** (or enrolled MFA).
4. API enforces ACL; NOK is **read-only** on sections.
5. Session is short: **5 min** Full Kit / **10 min** section + idle logout.

**Product fact:** Full Kit NOK *can* see highly personal owner data. That is intentional. Risk is managed by trust + short sessions + OTP, not by hiding Full Kit content from NOK.

Admin tools are designed for **metadata only** (counts, plans, billing) — not vault body decrypt.

---

## 2. Encryption model (critical for policy)

| Layer | What you have |
|-------|----------------|
| Vault sections **v3 (E2EE)** | Client AES-GCM; DEK wrapped with password; server stores opaque ciphertext only |
| Legacy sections **v2** | AES-256-GCM (+ AAD) with server `AES_256_KEY` until migrated |
| In transit | HTTPS / TLS (production; prefer TLS 1.3 at edge) |
| Session | HttpOnly JWT cookies · CSRF · MFA/OTP |
| Media | Authenticated Cloudinary + short signed URLs |

### End-to-end encryption (vault sections v3)

For `encryption_version: 3`, only Owner / authorized NOK browsers hold the DEK after password unlock.  
API, Mongo, and System Owner admin tools **cannot** decrypt those section payloads.

Legacy v2 rows still use server AES until auto-migrated on owner unlock/save.  
Messages, letters, and AI OCR may still use server AES — do not claim E2EE for those until migrated.

See [`E2EE.md`](./E2EE.md).

---

## 3. Residual risk audit (possibilities)

| Scenario | Impact | Mitigations already in product |
|----------|--------|--------------------------------|
| Stolen NOK card + OTP email access | High for granted areas | Short TTL · OTP · rate limit · CAPTCHA · needs E2EE wrap |
| Owner grants Full Kit to wrong person | Critical (process) | Section-limited option · revoke · UX care |
| XSS while owner logged in (DEK in session) | Critical | CSP nonce · CSRF · HttpOnly · escaped print · idle logout |
| `AES_256_KEY` / host leak | High for **legacy v2** only | Migrate to v3 · key rotation · prefer KMS |
| Admin account takeover | Medium (ops data, not v3 vault body) | Admin MFA · isolated cookies · no vault UI |
| Forgot-password without unlocked rewrap | High for existing v3 | Backup restore · document `needs_setup` |
| Unlocked device mid-NOK session | High until idle logout | Idle guard · 5–10 min JWT |

Highest residual: **Full Kit social trust**, **XSS while unlocked**, and **password-reset without rewrap**.

---

## 4. How secure is the user?

**Strong for a hosted vault product:**

- **E2EE** for vault sections (v3) — platform cannot read ciphertext  
- TLS in production  
- Clear Owner / NOK ACL  
- Admin cannot browse vault contents  
- Hardened login (CAPTCHA, rate limits, MFA/OTP, CSRF)  
- Short NOK sessions  
- Weekly security monitoring  

**Requires user + ops trust:**

- Owner must choose trusted NOK and access level  
- Password cards and OTP email must be protected  
- Legacy v2 / non-section surfaces until fully migrated  

**Honest one-liner for owners:**  
*Your kit sections use end-to-end encryption: after you sign in, only your browser (or a Next-of-Kin you authorize) can decrypt them. Our servers store ciphertext and cannot read those sections. Admins cannot open vault contents in admin tools.*

---

## 5. Confirmed policy / terms language (safe)

### You CAN say

- Vault **sections** use **end-to-end encryption** (AES-256-GCM in the browser; server cannot decrypt v3).  
- Personal data is also protected with **AES-256 at rest** for legacy rows and other surfaces.  
- Data is sent over **HTTPS/TLS**.  
- **Administrators cannot view vault section contents** through admin tools.  
- NOK access follows Owner settings (Full Kit or specific sections) and requires a key wrap from the owner.  
- Sessions use **HttpOnly cookies**, **CSRF protection**, **short lifetimes**, and **MFA/OTP**.  
- Weekly security monitoring and an eight-layer defense model.

### You MUST NOT say

- Unhackable / 100% secure / military-grade (absolute marketing)  
- E2EE for **messages / AI OCR / all product data** until those paths are migrated  
- Zero-knowledge for the entire platform (NOK delivery and legacy v2 still exist)

### Suggested policy paragraph (copy/paste)

> Orderly Affairs protects personal kit sections with end-to-end encryption: your browser holds the decryption key after sign-in, and our servers store only ciphertext for those sections. Next-of-Kin you authorize can unlock the same key after you share access. System administrators use separate tools for account and billing metadata and cannot browse vault section contents. Data in transit uses HTTPS/TLS. Some legacy records and non-section features may still use server-side encryption at rest until migrated. Owners remain responsible for choosing trusted Next-of-Kin and safeguarding login materials.

---

## 6. Before public “we are secure” claims

- [ ] `APP_ENV=production` on live servers  
- [ ] TLS / HTTPS working (prefer TLS 1.3 at edge)  
- [ ] Admin password changed from seed + MFA enrolled  
- [ ] `python scripts/verify_bank_grade.py` PASS  
- [ ] Owners have signed in once so v2→v3 migration can run  
- [ ] Terms use **E2EE for vault sections** wording per this doc  

Related: `docs/SECURITY_ARCHITECTURE.md` · `PRODUCTION_SETUP.md` · `app/security/KEY_ROTATION.md` · `docs/BACKUP.md` · `docs/E2EE.md` · `docs/SECURITY_PILLARS.md`

Daily backups archive Mongo documents **as stored** (v3 stays opaque ciphertext) into AES-GCM `.oa1b` packages — see `docs/BACKUP.md`.

---

## Appendix — 8-layer model & HttpOnly cookies

See full detail: [`docs/SECURITY_LAYERS.md`](./SECURITY_LAYERS.md)

**Confirmed:** Owner, NOK, and Admin sessions all use **HttpOnly** + **SameSite=Lax** + **Secure** (non-dev) cookies via `set_auth_cookie()` — same helper for all three portals. Portal CSRF client is already implemented.
