# SOC 2 Type I evidence pack — Orderly Affairs

This folder is what you hand a CPA. **It is not a SOC 2 certificate.**

## In-repo (done)

| Item | Path |
|------|------|
| System description | `system-description.md` |
| Control matrix CC1–CC9 | `control-matrix.md` |
| Policies | `01`–`10` |
| Risk register | `risk-register.md` |
| Vendors | `vendors.md` |
| Access review sheet | `access-review-template.md` |
| Pentest RFP | `pentest-scope.md` |
| Auditor steps | `auditor-type1.md` |
| Fail-closed check | `python scripts/verify_soc2_type1.py` |
| Restore drill | `python scripts/backup_restore_drill.py` |

## Company must still do (blocks the letter)

1. Fill `access-review-template.md` with **real names** and sign it.  
2. Download vendor SOC 2/ISO PDFs into `evidence/vendors/` (Mongo, AWS, Stripe, Cloudflare, Twilio, OpenAI).  
3. Hire an independent pentester (`pentest-scope.md`) and store the report.  
4. Sign a CPA **Type I** engagement (`auditor-type1.md`).  
5. On Hostinger: `APP_ENV=production`, Turnstile secret, ClamAV, `ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=false`.  
6. After Type I: keep quarterly reviews for **Type II** (usually 3–12 months).

Do not tell customers “SOC 2 certified” until the CPA report is issued.
