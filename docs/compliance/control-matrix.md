# Control matrix — Security TSC (CC1–CC9)

Map for Type I walkthrough. Evidence is in-repo unless marked **Company**.

| ID | Criterion (short) | Control | Evidence |
|----|-------------------|---------|----------|
| CC1.1 | Integrity / ethics | Info security + acceptable use policies | `01-information-security.md`, `10-acceptable-use-hr.md` |
| CC2.1 | Communication | System description; privacy notice | `system-description.md`, `06-privacy-dsr.md` |
| CC3.1 | Risk assessment | Annual risk register | `08-risk-assessment.md`, `risk-register.md` |
| CC4.1 | Monitoring | Weekly encryption monitor; 403 burst alerts | `weekly_monitor.py`, `vault_audit.py` |
| CC5.1 | Control activities | Fail-closed production checklist | `scripts/verify_soc2_type1.py` |
| CC6.1 | Logical access | MFA, RBAC/ABAC, admin cookie isolation | `docs/SECURITY_MODEL.md` |
| CC6.6 | Encryption | TLS + AES-256-GCM + SSM | `09-encryption-logging.md` |
| CC6.7 | Malware | CDR + ClamAV required in prod | `document_guard.py`, `malware_scan.py` |
| CC7.1 | Detect / log | vault_audit_logs ≥ 12 months | TTL 400 days |
| CC7.3 | Incidents | IR policy + security alerts | `03-incident-response.md` |
| CC8.1 | Change | PRs + template | `.github/pull_request_template.md`, `07-change-management.md` |
| CC9.2 | Vendors | Inventory + SOC reports | `vendors.md` **Company: download PDFs** |
| A1.2 | Backup | Daily `.oa1b` + restore drill | `04-backup.md`, `backup_restore_drill.py` |
| C1.1 | Confidentiality | Section grants; last-4 NOK | `vault_sensitive_fields.py` |
| P | Privacy / DSR | DSAR queue + purge | `06-privacy-dsr.md` |

**Company-only (cannot be done in git):** background checks, security training records, pentest report, CPA engagement, filled access review with real names, vendor SOC PDFs.
