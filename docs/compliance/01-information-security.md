# Information security policy

**Owner:** System owner admin  
**Review:** Annual, or after a material incident.

Orderly Affairs protects owner vault data with TLS in transit and AES-256-GCM at rest (AAD-bound). Production runs with `APP_ENV=production`, HttpOnly cookies, CSRF, MFA, rate limits, document rebuild (CDR), and ClamAV (`CLAMD_REQUIRED`).

Secrets live in AWS SSM / Secrets Manager, not git. Vault documents are not sent to VirusTotal or other third-party scanners.

Violations (sharing admin cookies, committing `.env`, disabling ClamAV in production) are incidents under `03-incident-response.md`.
