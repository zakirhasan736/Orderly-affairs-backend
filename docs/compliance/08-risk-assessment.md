# Risk assessment

**Cadence:** annual, or after SEV1.  
**Method:** likelihood × impact (1–5). Treat residual ≥ 12.

Top risks (see `risk-register.md`): credential theft, NOK over-sharing, backup key loss, vendor breach, malware in uploads, AI sending vault text to OpenAI.

Treatments: MFA, last-4 projector, ClamAV+CDR, SSM, encrypted backups with a dedicated key, vendor DPAs, owner consent for AI fill.

Output of each assessment is an updated register dated and stored under `evidence/`.
