# Incident response policy

**Owner:** System owner admin  
**Severity:** SEV1 vault/data exposure · SEV2 auth bypass or backup failure · SEV3 availability.

1. Contain (rotate keys, lock user, disable token).  
2. Record in `admin_security_alerts` / this folder’s `evidence/incidents/`.  
3. Notify affected owners if personal data was accessed.  
4. Fix and write a short post-incident note (timeline, cause, change).

403 bursts on vault APIs (`vault_403_burst`) are treated as SEV2 until proven benign.

Tabletop: run this playbook once per Type II window and store the dated notes.
