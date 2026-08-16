# Description of the system (SOC 2)

**Entity:** Orderly Affairs  
**System:** Owner vault + Next of Kin / Family portals + API  
**Period (Type I):** design as of the engagement date  
**Criteria:** Security (required). Confidentiality and Privacy in product; Availability via backups.

## Nature of the business

Orderly Affairs is a multi-party digital vault for estate and family continuity. Owners store sectioned life data. Family and Next of Kin receive **granted** views only.

## Infrastructure

- Client: Next.js portal (HttpOnly cookies, CSRF, Turnstile).  
- API: FastAPI on Hostinger VPS.  
- Data: MongoDB Atlas (AES-256-GCM ciphertext for vault fields).  
- Files: private S3 / signed URLs; uploads rebuilt (CDR) and ClamAV-scanned.  
- Secrets: AWS SSM Parameter Store.  
- Payments: Stripe. SMS MFA: Twilio. Edge: Cloudflare.

## Principals

Owner · Family collaborator · Next of Kin · System admin.

## Complementary user entity controls (CUECs)

Owners must: keep MFA enrolled, grant sections deliberately, understand device-only fields do not sync to other devices or NOK, and protect the vault unlock password for zero-knowledge fields.

## Subservice organizations

MongoDB, AWS, Stripe, Cloudflare, Twilio, OpenAI, Hostinger — see `vendors.md`.

## Statement

This description is for a licensed CPA. Orderly Affairs is **not SOC 2 certified** until that firm issues a Type I or Type II report.
