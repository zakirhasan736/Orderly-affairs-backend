# Client-side E2EE for vault sections

**Status:** **Disabled by default** (`E2EE_ENABLED=false`).

Product mode is **server AES-256-GCM (v2)** so owner, family, and NOK can all read granted sections after normal login. Client E2EE (v3) blocked shared access because the server cannot decrypt those rows for collaborators.

## AES-256-GCM is still the security grade

| Model | Cipher | Who holds the key? | Family / NOK sharing |
|-------|--------|--------------------|----------------------|
| **v2** (default) | AES-256-GCM | Server `AES_256_KEY` | Works — server decrypts for authorized sessions |
| **v3** (optional E2EE) | AES-256-GCM | Browser DEK only | Hard — every collaborator needs a DEK wrap |

**v2 is not “unencrypted.”** Data at rest in Mongo is still AES-256-GCM. Access is controlled by auth + ACL (owner / family / NOK).

Set `E2EE_ENABLED=true` only if you intentionally want server-blind vault ciphertext (and accept the wrap/unlock complexity).

## What “E2EE” means when enabled

| Party | Can decrypt vault section ciphertext (v3)? |
|-------|--------------------------------------------|
| Owner browser (after password unlock) | Yes — DEK in **tab memory** only |
| Next-of-Kin / family browser (if owner stored NOK wrap) | Yes — after master-password unlock |
| API / Mongo / System Owner admin tools | **No** — only opaque ciphertext + wrapped DEK |
| Server `AES_256_KEY` | **Not used** for `encryption_version: 3` |

When E2EE is turned off, owner login can still unwrap an existing DEK once to convert leftover v3 rows back to v2.

## Flow (default — server AES)

1. Owner / family / NOK sign in → authorized section APIs return decrypted JSON.
2. Section save → server encrypts with `AES_256_KEY` (v2).
3. Kit payload for NOK uses the same server decrypt path for granted sections.

## Flow (optional client E2EE)

1. Owner signs in with password → client derives wrapping key (PBKDF2-SHA256, 310k) → unwraps or creates DEK → holds DEK in memory (idle / hidden-tab auto-lock).
2. Server stores `{ salt, wrapped_dek }` on `users.e2ee` — never the DEK.
3. Section save → client AES-GCM encrypts JSON → `POST /e2ee/vault/{slug}` with `{ e2ee: true, ciphertext }`.
4. Section load → server returns ciphertext → client decrypts.
5. NOK/family: owner calls `POST /auth/e2ee/nok-wrap`; NOK unlocks on login.
6. On unlock, client **loops** migration of remaining v2 sections to v3 until `migration_complete` (or no progress). Dashboard banner auto-retries while unlocked.  
7. Password change while unlocked → `POST /auth/e2ee/rewrap`. Forgot-password reset clears the envelope (`needs_setup`).

Section 20/21 path aliases (`legal-documents-records` / `final-wishes`) resolve to the same E2EE gateway sections so migrate never 404s.

## XSS while unlocked (honest limit)

Any client-side DEK model has this residual: **script injection in an unlocked tab can call decrypt APIs**. Mitigations in product:

- DEK not in `sessionStorage` (no durable Application-tab scrape)
- Non-extractable `CryptoKey` used for encrypt/decrypt
- Idle + hidden-tab auto-lock shortens the window
- CSP nonce / `strict-dynamic` reduces XSS likelihood

This is not eliminated without moving decrypt off the browser (which would undo E2EE).

## APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/auth/e2ee/status` | Envelope metadata + wrapped DEK for client unwrap |
| POST | `/auth/e2ee/setup` | Store owner wrap |
| POST | `/auth/e2ee/rewrap` | Replace owner wrap (same DEK, new password) |
| GET | `/auth/e2ee/migration-status` | Count v2 vs v3 sections |
| POST | `/auth/e2ee/nok-wrap` | Store NOK wrap |
| GET/POST | `/e2ee/vault/{slug}` | Opaque section read/write |

## Limits

- **AI OCR autofill** still runs server-side on uploads; persisting into an E2EE section goes through the client encrypt path after fill.
- **Forgot-password** without an unlocked tab creates a **new** DEK — prior v3 ciphertext needs backup restore.
- **Reload / idle lock** clears DEK — user must unlock again.
- Messages / letters / kit embeds may still use server AES until migrated similarly.

## Claims

See `docs/SECURITY_PILLARS.md` — E2EE claim is valid **for vault sections at encryption_version 3**.
