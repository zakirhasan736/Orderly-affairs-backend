# Cryptographic Key Rotation

Orderly Affairs uses two classes of signing/encryption keys. Rotate them on a fixed schedule and document each rotation in your deployment runbook.

## JWT RS256 (access tokens)

**Schedule:** quarterly (every 90 days)

**Keys:** `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` (or `keys/private.pem` and `keys/public.pem`)

**Overlap key:** `JWT_PREVIOUS_PUBLIC_KEY` or `keys/public.previous.pem` (verify-only)

**Procedure:**

1. Generate a new RSA key pair:
   ```bash
   python scripts/generate_jwt_keys.py --force
   ```
   This writes new PEMs and copies the old public key to `keys/public.previous.pem`.
2. Deploy the new **private** + **public** keys together with the previous public key.
3. Restart the API. New tokens are signed with the new private key; `verify_token` accepts either public key.
4. Keep the previous public key until all outstanding access tokens expire (default 15 minutes) **plus** refresh-token lifetime (default 7 days). Optionally force-logout by revoking refresh tokens.
5. Remove `public.previous.pem` / `JWT_PREVIOUS_PUBLIC_KEY` after the overlap window.
6. Record rotation date, operator, and key id in your security log.

**Config:** `JWT_KEY_ROTATION_DAYS` in `app/config.py` (default 90) documents the intended interval; enforce via calendar reminders or automation.

## AES-256-GCM (data at rest)

**Schedule:** annual, or per organizational policy (e.g. after personnel change, suspected compromise, or compliance audit).

**Key:** `AES_256_KEY` (32-byte value, base64-encoded in environment)

**Overlap key:** `AES_256_KEY_PREVIOUS` (old key, decrypt-only during migration)

**Procedure:**

1. Generate a new 32-byte key:
   ```bash
   python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
   ```
2. Set env:
   - `AES_256_KEY=<new key>`
   - `AES_256_KEY_PREVIOUS=<old key>`
3. Restart the API. `decrypt_data` tries the current key, then the previous key.
4. Re-encrypt all ciphertext under the new key:
   ```bash
   python scripts/rotate_aes_key.py --dry-run
   python scripts/rotate_aes_key.py
   ```
5. Verify decrypt/encrypt on a sample of vault sections, messages, TOTP, and AI extracts.
6. Remove `AES_256_KEY_PREVIOUS` and restart. Securely destroy the old key material after validation.

**Note:** AES rotation requires re-encrypting stored ciphertext. Plan a maintenance window; JWT rotation does not.

## Emergency rotation

Rotate immediately if a key is exposed, a host is compromised, or an operator with key access leaves the organization. For JWT, follow the overlap procedure above. For AES, prioritize re-encryption and invalidate active sessions if the breach may include live tokens.

## CSRF (cookie sessions)

Mutating API calls that carry auth cookies require a matching `X-CSRF-Token` header (double-submit). The API sets `oa_csrf_token` and echoes the value in the `X-CSRF-Token` response header so the portal can cache it across origins.

- Toggle: `CSRF_PROTECTION_ENABLED` (default `true`)
- Exempt: `/billing/webhook`, OpenAPI/docs, health

## Verification

```bash
python scripts/verify_bank_grade.py
python scripts/migrate_ai_security.py --dry-run
python scripts/migrate_media_security.py --dry-run
python scripts/rotate_aes_key.py --dry-run --allow-without-previous
```

Record rotation dates and operators in your deployment security log.
