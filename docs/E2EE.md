# Client-side E2EE for vault sections

**Status:** Implemented behind `E2EE_ENABLED=true` (default).

## AES-256-GCM is good — key custody is the issue

| Model | Cipher | Who holds the key? | If API / Mongo / admin is compromised |
|-------|--------|--------------------|----------------------------------------|
| **v2** (legacy) | AES-256-GCM | Server `AES_256_KEY` | Attacker can decrypt vault section bodies |
| **v3** (E2EE) | AES-256-GCM (same algorithm) | Browser DEK only; server stores *wrapped* DEK | Attacker sees opaque ciphertext only |

So: **we are not replacing a weak cipher.** We are moving from *server-held keys* to *client-held keys*. AES-256-GCM remains the right authenticated encryption choice in both models.

## What “E2EE” means here

| Party | Can decrypt vault section ciphertext (v3)? |
|-------|--------------------------------------------|
| Owner browser (after password unlock) | Yes — DEK in **tab memory** only |
| Next-of-Kin / family browser (if owner stored NOK wrap) | Yes — after master-password unlock |
| API / Mongo / System Owner admin tools | **No** — only opaque ciphertext + wrapped DEK |
| Server `AES_256_KEY` | **Not used** for `encryption_version: 3` |

Legacy `encryption_version: 2` rows remain server-decryptable until migrated (owner unlock runs a full section pass).

## Flow

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
