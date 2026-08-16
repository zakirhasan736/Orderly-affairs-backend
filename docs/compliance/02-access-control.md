# Access control policy

**Owner:** System owner admin  
**Review:** Quarterly (see `access-review-template.md`).

Principals: Owner, Family, Next of Kin, System admin. Vault section grants are ABAC. Admin APIs require the admin cookie/Bearer — **no owner-cookie fallback in production** (`ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=false`).

Access to GitHub, AWS, MongoDB Atlas, Hostinger, and Stripe is named, MFA-protected, and least-privilege. Offboarding: revoke the same day (GitHub seat, AWS IAM, Atlas user, Hostinger SSH, Stripe team).

Joiner/mover/leaver is recorded on the quarterly access review sheet.
