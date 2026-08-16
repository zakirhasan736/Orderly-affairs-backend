# Risk register (design)

Date: 2026-08-15 · Owner: system admin · Next review: 2027-08-15

| ID | Risk | L | I | Score | Treatment | Status |
|----|------|---|---|-------|-----------|--------|
| R1 | Stolen owner session | 3 | 5 | 15 | 15m JWT, MFA, HttpOnly, CSRF | In product |
| R2 | NOK sees full bank numbers | 3 | 5 | 15 | Last-4 projector; docs device-only | In product |
| R3 | Malware in uploads | 3 | 4 | 12 | CDR + ClamAV fail-closed in prod | In product |
| R4 | Backup key lost | 2 | 5 | 10 | Dedicated BACKUP_ENCRYPTION_KEY in SSM; offline copy **Company** | Partial |
| R5 | Atlas / AWS breach | 2 | 5 | 10 | Ciphertext at rest; vendor SOC **Company** | Partial |
| R6 | OpenAI retains prompts | 3 | 4 | 12 | Owner-initiated fill only; no VirusTotal; DPA **Company** | Partial |
| R7 | Admin via owner cookie | 2 | 5 | 10 | Fallback off in production | In product |
| R8 | No restore tested | 3 | 4 | 12 | `backup_restore_drill.py` | In product |
| R9 | No independent pentest | 4 | 4 | 16 | `pentest-scope.md` — **hire tester** | Open |
| R10 | Claim SOC 2 without letter | 2 | 5 | 10 | Public wording: aligned, not certified | Policy |
