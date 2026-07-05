# Cryptographic Key Rotation

Orderly Affairs uses two classes of signing/encryption keys. Rotate them on a fixed schedule and document each rotation in your deployment runbook.

## JWT RS256 (access tokens)

**Schedule:** quarterly (every 90 days)

**Keys:** `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` (or `keys/private.pem` and `keys/public.pem`)

**Procedure:**

1. Generate a new RSA key pair (2048-bit minimum).
2. Deploy the new **private** key to the API; keep the previous **public** key available during overlap.
3. Sign new tokens with the new private key. Accept verification with either the current or previous public key until all outstanding access tokens expire (default 15 minutes) plus refresh-token lifetime (default 7 days).
4. Remove the old public key after the overlap window.
5. Record rotation date, operator, and key id in your security log.

**Config:** `JWT_KEY_ROTATION_DAYS` in `app/config.py` (default 90) documents the intended interval; enforce via calendar reminders or automation.

## AES-256-GCM (data at rest)

**Schedule:** annual, or per organizational policy (e.g. after personnel change, suspected compromise, or compliance audit).

**Key:** `AES_256_KEY` (32-byte value, base64-encoded in environment)

**Procedure:**

1. Generate a new 32-byte key: `python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"`.
2. Run the at-rest re-encryption migration (`app/security/encrypt_at_rest_migration.py`) with the new key, or use your documented dual-key decrypt/re-encrypt process.
3. Update `AES_256_KEY` in the deployment environment.
4. Verify decrypt/encrypt on a sample of vault sections, messages, and Next-of-Kin profiles.
5. Securely destroy the old key material after validation.

**Note:** AES rotation requires re-encrypting stored ciphertext. Plan a maintenance window; JWT rotation does not.

## Emergency rotation

Rotate immediately if a key is exposed, a host is compromised, or an operator with key access leaves the organization. For JWT, follow the overlap procedure above. For AES, prioritize re-encryption and invalidate active sessions if the breach may include live tokens.
