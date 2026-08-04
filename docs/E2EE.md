# Client-side E2EE for vault sections (development)

**Status:** Implemented behind `E2EE_ENABLED=true` (default).

## What “E2EE” means here

| Party | Can decrypt vault section ciphertext (v3)? |
|-------|--------------------------------------------|
| Owner browser (after password unlock) | Yes — holds DEK in `sessionStorage` for the tab |
| Next-of-Kin / family browser (if owner stored NOK wrap) | Yes — after master-password unlock |
| API / Mongo / System Owner admin tools | **No** — only opaque ciphertext + wrapped DEK |
| Server `AES_256_KEY` | **Not used** for `encryption_version: 3` |

This is **true end-to-end for vault sections** once a section is under E2EE v3.  
Legacy `encryption_version: 2` rows remain server-decryptable until migrated (auto on owner unlock / save).

## Flow

1. Owner signs in with password → client derives wrapping key (PBKDF2-SHA256, 310k) → unwraps or creates DEK → stores DEK only in session memory.  
2. Server stores `{ salt, wrapped_dek }` on `users.e2ee` — never the DEK.  
3. Section save → client AES-GCM encrypts JSON → `POST /e2ee/vault/{slug}` with `{ e2ee: true, ciphertext }`.  
4. Section load → server returns ciphertext → client decrypts.  
5. NOK/family: owner calls `POST /auth/e2ee/nok-wrap` with wrap from card password; NOK unlocks on login.  
6. On unlock, client migrates remaining v2 sections to v3 in the background.  
7. Password change while unlocked → `POST /auth/e2ee/rewrap`. Forgot-password reset clears the envelope (`needs_setup`); next login creates a new DEK (restore v3 from backup if needed).

## APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/auth/e2ee/status` | Envelope metadata + wrapped DEK for client unwrap |
| POST | `/auth/e2ee/setup` | Store owner wrap |
| POST | `/auth/e2ee/rewrap` | Replace owner wrap (same DEK, new password) |
| GET | `/auth/e2ee/migration-status` | Count v2 vs v3 sections |
| POST | `/auth/e2ee/nok-wrap` | Store NOK wrap |
| GET/POST | `/e2ee/vault/{slug}` | Opaque section read/write |

## Limits (honest)

- **AI OCR autofill** still runs server-side on uploads; persisting into an E2EE section goes through the client encrypt path after fill.  
- **Forgot-password** without an unlocked tab creates a **new** DEK — prior v3 ciphertext needs backup restore.  
- **Tab close** clears DEK (`sessionStorage`) — user must sign in again.  
- Messages / letters / kit embeds may still use server AES until migrated similarly.

## Claims

See `docs/SECURITY_PILLARS.md` — E2EE claim is valid **for vault sections at encryption_version 3**.
