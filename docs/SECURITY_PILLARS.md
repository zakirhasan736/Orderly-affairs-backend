# Orderly Affairs — Security pillars (approved public wording)

Use this order in marketing, Terms, and Trust pages.  
Date: Aug 4, 2026

---

## Lead with these four

### 1. Weekly security monitoring and logging
**Status: implemented**

- Automated weekly job audits encryption integrity of vault sections / kit data and related stores.
- Records results in the **admin audit log** and raises **Security** alerts when issues or MFA gaps appear.
- Continuous operational logging: auth rate limits, admin actions, backup run/restore.

Ops: `app/security/weekly_monitor.py` · Admin → Security · Admin → Audit log  
Env: `WEEKLY_SECURITY_MONITOR_*` (default Sunday 04:30 UTC)

### 2. Eight-layer security for user / section data
**Status: implemented** — see [`SECURITY_LAYERS.md`](./SECURITY_LAYERS.md)

Defense in depth for Owner, Next-of-Kin, and Admin around personal kit / section data:

1. HTTPS / TLS in transit  
2. HttpOnly Secure cookies + CSRF  
3. CAPTCHA + rate limits  
4. MFA / OTP  
5. Authorization (Owner / NOK ACL; admin has no vault UI)  
6. **AES-256-GCM encryption at rest** (section & personal fields)  
7. Authenticated media + signed URLs  
8. Portal CSP + idle session controls  

### 3. Who can decrypt personal section data?
**Status: E2EE for vault sections (v3)** — see [`E2EE.md`](./E2EE.md)

| Claim | Allowed? |
|-------|----------|
| System Owner / Admin **tools cannot open vault section contents** | ✅ Yes |
| Vault sections saved under **E2EE (encryption_version 3)** — server **cannot** decrypt | ✅ Yes |
| Owner / authorized NOK decrypt **only in the browser** after password unlock | ✅ Yes |
| Legacy sections (v2) still use server AES until re-saved | ⚠️ Migrate on save |

**Safe one-liner:**

> Personal kit sections use end-to-end encryption: your browser holds the decryption key after sign-in. Our servers and system administrators store only ciphertext and cannot read section contents.

### 4. HTTPS everywhere · AES-256 at rest · TLS in transit
**Status: implemented (enforce in production)**

Approved checklist for Trust / homepage:

- ✅ **HTTPS everywhere** (redirect + HSTS when `APP_ENV=production`)  
- ✅ **AES-256 encryption at rest** (AES-256-GCM for vault / section payloads)  
- ✅ **TLS in transit** (terminate TLS 1.2+ at load balancer / reverse proxy; prefer **TLS 1.3** on the edge)

> Note: Application code speaks HTTPS to clients via your reverse proxy (nginx, Cloudflare, ALB, etc.). Confirm the edge is configured for TLS 1.3 before publishing “TLS 1.3” specifically.

---

## Copy-paste Trust bullets (safe)

```text
• Weekly security monitoring and logging
• Eight-layer defense for personal kit and section data
• End-to-end encryption for vault sections — servers cannot decrypt (v3)
• HTTPS everywhere · TLS in transit (TLS 1.3 at the edge)
• AES-256-GCM at rest for legacy rows until migrated
```

## Do not publish

```text
• Unhackable / 100% secure
• E2EE for every product surface (messages/AI) until those paths are migrated
```

---

Related: [`PERSONAL_DATA_SECURITY_CLAIMS.md`](./PERSONAL_DATA_SECURITY_CLAIMS.md) · [`SECURITY_LAYERS.md`](./SECURITY_LAYERS.md) · [`BACKUP.md`](./BACKUP.md)
