# Orderly Affairs — Official Security Model

**For fire departments, organizational clients, and compliance review.**

Version 1.0 · August 2026

This document is the **official security specification** for how Orderly Affairs protects kit owner data when it is shared with **Family collaborators** and **Next of Kin (NOK)**.

Related technical references:

- `docs/SECURITY_ARCHITECTURE.md` — system topology and controls
- `docs/SECURITY_LAYERS.md` — eight-layer defense in depth
- `docs/E2EE.md` — optional client encryption mode

---

## 1. Security model summary

Orderly Affairs uses a **Regulated Multi-Party Vault** model:

| Pillar | Implementation |
|--------|----------------|
| **Three principals** | Owner · Family collaborator · Next of Kin |
| **Two portals** | Owner dashboard · NOK dashboard |
| **RBAC** | Family portal roles (Viewer → Super Admin) |
| **ABAC** | Per-person section and dashboard area grants |
| **Encryption** | AES-256-GCM at rest · TLS in transit |
| **Authentication** | MFA · HttpOnly cookies · CSRF |
| **Audit** | Immutable vault access log |
| **Lifecycle** | Revoke · billing lock · death workflow |

This is the industry-appropriate model for **shared legacy vaults** (estate planning, first-responder kits, organizational continuity) — not a single-user zero-knowledge password manager.

---

## 2. Three security principals

### 2.1 Owner

| Attribute | Value |
|-----------|--------|
| **Principal ID** | `owner` |
| **Portal** | Owner dashboard (`/dashboard`) |
| **Session** | `auth_token` cookie · ~15 minute access TTL |
| **Vault access** | Full read and write on all sections |
| **Management** | Invite family · configure NOK · billing · MFA |

### 2.2 Family collaborator

| Attribute | Value |
|-----------|--------|
| **Principal ID** | `family` |
| **Portal** | Owner dashboard (`/dashboard`) — **not** the NOK portal |
| **Login** | `/family/login` |
| **Session** | `nok_auth_token` cookie (isolated namespace) + `access_type=family` |
| **Vault access** | Read/write **only** on owner-granted dashboard areas |
| **RBAC** | Portal role controls capabilities (see §4) |

Family members help the owner maintain the kit. They do **not** use the NOK survivor dashboard.

### 2.3 Next of Kin (NOK)

| Attribute | Value |
|-----------|--------|
| **Principal ID** | `nok` |
| **Portal** | NOK dashboard (`/next-kin/*`) — **separate** from owner dashboard |
| **Login** | `/next-kin` |
| **Session** | `nok_auth_token` cookie · **5–10 minute** access TTL |
| **Vault access** | **Read-only** on owner-granted sections |
| **Special actions** | Checklists · report passing · message delivery (after death rules) |

NOK is a **survivor access portal**, not an editor role.

---

## 3. Two portals

```
┌─────────────────────────────┐     ┌─────────────────────────────┐
│     OWNER DASHBOARD         │     │      NOK DASHBOARD          │
│  /dashboard                 │     │  /next-kin/dashboard        │
│                             │     │                             │
│  Owner ── full control      │     │  NOK ── read-only sections  │
│  Family ── granted areas    │     │  checklists · death flow    │
└─────────────────────────────┘     └─────────────────────────────┘
         ▲                                    ▲
         │                                    │
    Owner cookie                         NOK cookie
    or family session                    (NOK principal only)
```

**Enforcement:** API middleware blocks family sessions from NOK-only endpoints. Frontend redirects family users away from `/next-kin/*`.

---

## 4. RBAC — family portal roles

Role-Based Access Control defines **what a family role can do in general**.

| Portal role | Read sections | Write sections | Upload | Manage family | Manage NOK | View billing |
|-------------|:-------------:|:--------------:|:------:|:-------------:|:----------:|:------------:|
| **Viewer** | ✅ granted | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Editor** | ✅ granted | ✅ granted | ✅ | ❌ | ❌ | ❌ |
| **Portal Manager** | ✅ granted | ✅ granted | ✅ | ✅ | ❌ | ❌ |
| **Admin** | ✅ granted | ✅ granted | ✅ | ✅ | ✅ | ❌ |
| **Super Admin** | ✅ granted | ✅ granted | ✅ | ✅ | ✅ | ✅ view |

**Code reference:** `app/auth/portal_roles.py`  
**Enforcement:** `assert_rbac_family_permission()` in `app/security/vault_principals.py`

NOK has **no portal roles** — API write operations are always denied for NOK principals.

---

## 5. ABAC — section and area grants

Attribute-Based Access Control defines **what this specific person can access**.

### 5.1 Attributes evaluated

| Attribute | Used for |
|-----------|----------|
| `owner_id` | Which kit the collaborator belongs to |
| `access_type` | `family` vs `nextkin` (principal separation) |
| `access_level` | Full kit/dashboard vs section/area specific |
| `authorized_sections` | List of section or area IDs granted |
| `immediate_access` | Owner approval (required for all collaborators) |
| `access_revoked` | Owner revocation |
| `owner_status` | Alive / deceased (death workflow) |
| `billing.status` | Payment lock on vault APIs |

### 5.2 Section read (owner, family, NOK)

**Rule:** Allow read on section `S` if:

- Principal is **owner**, OR
- Principal is **family** or **NOK**, AND `immediate_access=true`, AND
  - `access_level` is Full Kit / Full Dashboard, OR
  - `S` (or parent section digit) is in `authorized_sections`

**NOK additional rule:** Sections **2, 3, 4** (access management, letters, messages) are **denied** on NOK portal APIs even if mistakenly granted.

**Code reference:** `assert_abac_vault_section_read()` → `app/security/vault_principals.py`

### 5.3 Dashboard area read (family only)

Family collaborators may also be granted special dashboard areas:

- `overview` — dashboard home
- `billing` — subscription view (Super Admin)
- `vault_settings` — family role management
- `section2_nextkin` — NOK management (Admin+)
- Vault section IDs (`"5"`, `"7"`, …)

**Code reference:** `assert_abac_family_dashboard_area()` → `app/auth/family_access.py`

---

## 6. AES-256-GCM encryption at rest

All personal vault payloads are encrypted before storage in MongoDB.

| Property | Detail |
|----------|--------|
| **Algorithm** | AES-256-GCM (authenticated encryption) |
| **Key** | 32-byte `AES_256_KEY` (environment / AWS Secrets Manager) |
| **Context binding (AAD)** | e.g. `section:{owner_id}:{section_id}` |
| **Format** | v2: `base64(0x02 + nonce + ciphertext + tag)` |
| **In transit** | TLS 1.2+ (HTTPS only in production) |

**What encryption protects:** Database compromise, backup theft, insider MongoDB access without the key.

**What encryption does not hide from collaborators:** After successful login and ACL check, the server decrypts granted sections for family/NOK display. This is **required** for shared legacy vault operation.

**Optional E2EE:** Client-side encryption (v3) can be enabled per deployment; default is server AES for family/NOK sharing. See `docs/E2EE.md`.

**Code reference:** `app/security/crypto.py`, `app/security/section_crypto.py`

---

## 7. Authentication and sessions

| Control | Owner | Family | NOK |
|---------|-------|--------|-----|
| HttpOnly cookies | ✅ | ✅ | ✅ |
| Secure + SameSite=Lax | ✅ | ✅ | ✅ |
| CSRF on mutations | ✅ | ✅ | ✅ |
| MFA | When enrolled | On login | **Forced** on every login |
| CAPTCHA (Turnstile) | ✅ | ✅ | ✅ |
| Rate limiting | ✅ | ✅ | ✅ |
| Session TTL | ~15 min | ~15 min | **5–10 min** |
| Idle logout (frontend) | ✅ | ✅ | ✅ (shorter) |

Login clears other portal cookies to prevent session mixing.

---

## 8. Immutable vault audit

Every vault API request is logged to `vault_audit_logs`:

| Field | Example |
|-------|---------|
| `actor_id` | Collaborator or owner ID |
| `actor_role` | JWT role |
| `access_type` | `family` / `nextkin` / null (owner) |
| `portal_role` | `editor` (family only) |
| `owner_id` | Kit owner |
| `section_id` | Extracted from path when present |
| `action` | `read` or `write` |
| `method` / `path` | HTTP details |
| `status_code` | Response |
| `ip` | Client IP |
| `timestamp` | UTC |

**Policy:** Audit records are append-only; deletion requires administrative database procedure with separate audit trail.

**Code reference:** `app/security/vault_audit.py`

---

## 9. Lifecycle controls

### 9.1 Revoke access

Owner can revoke family or NOK access (`immediate_access=false`, `access_revoked=true`). Revoked users fail API authorization immediately.

### 9.2 Billing lock

When owner billing is `past_due` or blocked:

- Vault read/write APIs return **403** (middleware)
- Owner can still sign in to update payment
- NOK/family vault calls are blocked (kit owner billing is checked)

**Code reference:** `app/security/vault_billing_middleware.py`

### 9.3 Death workflow

| Step | Behavior |
|------|----------|
| NOK checklist | Death-related checklist items increment signals — **no auto-deceased** |
| Threshold | ≥2 signals or `death_signals_pending_confirmation` |
| Report passing | NOK principal only · password + confirm · rate limited |
| Upon-death NOK | `immediate_access` granted to upon-death NOK records |
| Owner self-status | Owner **cannot** set `deceased` via API — must use NOK report flow |

**Code reference:** `app/auth/death_detection.py`, `app/auth/routes.py`

---

## 10. Defense in depth (layer stack)

```
Layer 8  Portal hardening     CSP · idle logout · sensitive storage wipe
Layer 7  Principal separation Owner | Family | NOK middleware + ABAC
Layer 6  RBAC                 Family portal roles
Layer 5  ABAC                 Section / area grants per person
Layer 4  MFA + rate limits    Step-up identity
Layer 3  Session cookies      HttpOnly · CSRF · isolated namespaces
Layer 2  TLS                  HTTPS only
Layer 1  AES-256-GCM          Encrypted at rest with AAD context binding
```

---

## 11. Compliance-oriented statements

For client security questionnaires:

1. **Data at rest** is encrypted with **AES-256-GCM** with per-record authenticated context.
2. **Access** follows **least privilege**: owner grants explicit sections/areas; NOK is read-only.
3. **Separation of duties**: family collaborators use the owner dashboard; NOK uses a separate portal.
4. **Audit trail** records vault API access with actor, role, and outcome.
5. **Account lifecycle** supports revoke, billing suspension, and documented death workflow.
6. **MFA** is available for owners and required for NOK login.

---

## 12. Implementation map

| Concern | Module |
|---------|--------|
| Principals (owner/family/nok) | `app/security/vault_principals.py` |
| ABAC section read | `assert_abac_vault_section_read()` |
| RBAC family permissions | `assert_rbac_family_permission()` |
| ABAC dashboard areas | `app/auth/family_access.py` |
| NOK-only API guard | `VaultPrincipalMiddleware` |
| Vault audit | `app/security/vault_audit.py` |
| Billing lock | `app/security/vault_billing_middleware.py` |
| AES-256-GCM | `app/security/crypto.py` |
| Family portal roles | `app/auth/portal_roles.py` |
| Frontend NOK guard | `src/app/next-kin/layout.tsx` |

---

## 13. Contact and review

For security review requests, penetration test scope, or organizational onboarding, reference this document version and deployment environment (`APP_ENV`, `E2EE_ENABLED`).

**Document owner:** Orderly Affairs engineering  
**Classification:** Client-shareable security specification
