# Daily encrypted backups (Mongo user data)

## What this does

Every day (default **03:00 UTC**), the API exports selected MongoDB collections **as stored** (vault fields stay AES-GCM ciphertext), packs them into a `.tar.gz`, then encrypts the archive with **AES-256-GCM** into:

```text
storage/backups/orderly-backup-YYYYMMDD-HHMMSS.oa1b
storage/backups/orderly-backup-YYYYMMDD-HHMMSS.oa1b.manifest.json
```

This is **not** end-to-end encryption and **not** a substitute for MongoDB Atlas snapshots. It is an application-level disaster-recovery package of user/kit data.

## Admin panel

Super Admins can open **Governance → Backups** (`/admin/backups`):

| Action | Who | API |
|--------|-----|-----|
| List local packages | Admin area `backups` | `GET /admin/backups` |
| Run backup now | Super Admin | `POST /admin/backups/run` |
| Restore package | Super Admin | `POST /admin/backups/{filename}/restore` body `{"confirm":"RESTORE"}` |

Restore **replaces** collections from the package (documents stay ciphertext). A fresh safety `.oa1b` is written first. Every run/restore is written to the admin audit log.

---

## Local (default)

| Env | Default | Meaning |
|-----|---------|---------|
| `BACKUP_ENABLED` | `true` | Start cron on API startup |
| `BACKUP_ROOT` | `storage/backups` | Local output (gitignored) |
| `BACKUP_CRON_HOUR` / `BACKUP_CRON_MINUTE` | `3` / `0` | Cron time (scheduler TZ = machine/UTC) |
| `BACKUP_RETENTION_DAYS` | `14` | Delete older local `.oa1b` files |
| `BACKUP_INCLUDE_VAULT_FILES` | `false` | Also copy `VAULT_ROOT` disk files |
| `BACKUP_ENCRYPTION_KEY` | _(unset)_ | Preferred 32-byte key (base64). If unset, uses `AES_256_KEY` |

Generate a dedicated backup key (store offline / secrets manager — needed to open packages):

```bash
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

### Manual run

```bash
python scripts/run_backup.py
python scripts/run_backup.py --no-s3
```

### Decrypt (restore drill)

```bash
python scripts/decrypt_backup.py storage/backups/orderly-backup-….oa1b
tar -xzf storage/backups/orderly-backup-….tar.gz
# then mongoimport each payload/mongo/*.ndjson
```

Opening vault ciphertext after import still requires `AES_256_KEY` (and previous key if rotating).

## AWS S3 (versioned)

1. Create a private S3 bucket.
2. **Enable Versioning** on the bucket (AWS console → Properties → Bucket Versioning).
3. Prefer **Block Public Access** + IAM least privilege (`s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on the prefix).
4. Optional: lifecycle rules to expire noncurrent versions after N days.
5. Set env:

```env
BACKUP_S3_ENABLED=true
BACKUP_S3_BUCKET=your-bucket-name
BACKUP_S3_PREFIX=orderly-affairs/backups
BACKUP_S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Install dependency:

```bash
pip install boto3
```

Objects land at:

```text
s3://{bucket}/{prefix}/{YYYY}/{MM}/{DD}/orderly-backup-….oa1b
```

Uploads use S3 SSE-S3 (`AES256`) **in addition to** the app-level `.oa1b` encryption.

Force upload once:

```bash
python scripts/run_backup.py --s3
```

## Security notes

- Prefer `BACKUP_ENCRYPTION_KEY` separate from `AES_256_KEY` so a leaked app key alone does not open offline packages without the backup key (and vice versa for vault decrypt after restore).
- Do not commit `storage/backups/` or keys.
- Sidecar `.manifest.json` has counts and SHA-256 only — no vault plaintext.
- Ephemeral auth collections (`otp*`, `refresh_tokens`, …) are skipped by design.

## Collections included

See `app/backup/collections.py` (`BACKUP_COLLECTIONS`).
