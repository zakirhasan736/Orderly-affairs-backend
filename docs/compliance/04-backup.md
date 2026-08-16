# Backup policy

**Owner:** System owner admin  
**RPO:** 24 hours (daily `.oa1b` + Atlas snapshots). **RTO:** restore drill target ≤ 8 hours.

Daily encrypted Mongo export (`BACKUP_ENABLED=true`) plus S3 versioning. Production requires `BACKUP_ENCRYPTION_KEY` (not the live AES vault key).

Restore **never** targets the live `orderly_affairs` database during a drill. Use `scripts/backup_restore_drill.py` against a throwaway DB name and file the dated result under `evidence/`.

Retain local packages per `BACKUP_RETENTION_DAYS`; keep S3 versions ≥ 30 days.
