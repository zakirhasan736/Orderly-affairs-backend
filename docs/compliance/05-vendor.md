# Vendor management policy

**Owner:** System owner admin  
**Review:** Annual, or when adding a processor.

Subprocessors that can touch customer data or auth: MongoDB Atlas, AWS, Stripe, Cloudflare, Twilio, OpenAI (AI fill), Hostinger (compute).

Before go-live and annually: download each vendor’s SOC 2 or ISO report from their trust center into `evidence/vendors/` and tick `vendors.md`. No new vendor without a DPA and a listed trust report (or a documented exception).
