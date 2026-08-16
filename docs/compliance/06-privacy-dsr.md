# Privacy and data-subject requests (DSR)

**Owner:** System owner admin  
**Rights:** access, export, delete (account purge), correct via the owner vault.

Owners control server vs zero-knowledge vs this-device storage. Next of Kin receive only granted sections and **last 4** of account/policy/ID numbers — never full PAN, passwords, or statement files.

DSR tickets go through the admin DSAR queue (`admin_dsar_requests`). Delete uses the account purge path (Mongo + S3/Cloudinary prefixes). Device-only fields cannot be retrieved from the server — state that honestly in the public privacy notice.

Do not use vault documents to train public models.
