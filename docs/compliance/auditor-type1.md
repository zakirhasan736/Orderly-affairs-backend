# Type I auditor engagement

SOC 2 Type I is a **CPA** report on control design. Type II needs 3–12 months of the same controls operating.

## What we prepare (done in-repo)

- Policies `01`–`06`
- Vendor inventory + trust-center links
- Access-review template
- `verify_soc2_type1.py` production fail-closed checks
- Audit log TTL ≥ 12 months + 403 burst alerts
- Backup restore drill script

## What you still do as the company

1. Shortlist a CPA firm or a platform that partners with one (Vanta / Drata / Secureframe + auditor).  
2. Sign an engagement letter for **Security TSC Type I**.  
3. Grant the auditor read-only evidence (this folder + screenshots of MFA, Atlas, AWS, Hostinger).  
4. After Type I, keep filling quarterly reviews and incident notes for the Type II window.  
5. Do not market “SOC 2 certified” until the letter is issued.

Engagement status: **not signed** (runbook only).
