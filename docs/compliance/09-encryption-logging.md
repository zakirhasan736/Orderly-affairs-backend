# Encryption and logging

In transit: TLS. At rest: AES-256-GCM with AAD for vault sections, messages, TOTP secrets. Backup archives use `BACKUP_ENCRYPTION_KEY`. JWT is RS256.

Keys: SSM / Secrets Manager; rotation notes in `KEY_ROTATION.md`. Production refuses a missing AES key.

Logging: `vault_audit_logs` for vault API access (actor, path, status, IP). Retention `VAULT_AUDIT_RETENTION_DAYS` ≥ 365 (default 400). 403 bursts raise `admin_security_alerts`. Weekly monitor audits encryption health.

Logs are not a substitute for encryption. Do not log full account numbers, passwords, or MFA secrets.
