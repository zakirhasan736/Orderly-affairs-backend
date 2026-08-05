# AWS secrets on Hostinger VPS
#
# Console list under `/orderly-affairs/*` (type **SecureString**) is
# **Systems Manager Parameter Store**. The app loads them with `GetParametersByPath`.

## Verified inventory (17 parameters)

| AWS parameter | App env var |
|---------------|-------------|
| `/orderly-affairs/MONGODB_URI` | `MONGO_URL` (auto-mapped) |
| `/orderly-affairs/AES_256_KEY` | `AES_256_KEY` |
| `/orderly-affairs/BACKUP_ENCRYPTION_KEY` | `BACKUP_ENCRYPTION_KEY` |
| `/orderly-affairs/JWT_PRIVATE_KEY` | `JWT_PRIVATE_KEY` |
| `/orderly-affairs/JWT_PUBLIC_KEY` | `JWT_PUBLIC_KEY` |
| `/orderly-affairs/SENDGRID_API_KEY` | `SENDGRID_API_KEY` |
| `/orderly-affairs/OPENAI_API_KEY` | `OPENAI_API_KEY` |
| `/orderly-affairs/STRIPE_SECRET_KEY` | `STRIPE_SECRET_KEY` |
| `/orderly-affairs/STRIPE_WEBHOOK_SECRET` | `STRIPE_WEBHOOK_SECRET` |
| `/orderly-affairs/TURNSTILE_SECRET_KEY` | `TURNSTILE_SECRET_KEY` |
| `/orderly-affairs/TWILIO_ACCOUNT_SID` | `TWILIO_ACCOUNT_SID` |
| `/orderly-affairs/TWILIO_AUTH_TOKEN` | `TWILIO_AUTH_TOKEN` |
| `/orderly-affairs/TWILIO_PHONE_NUMBER` | `TWILIO_PHONE_NUMBER` |
| `/orderly-affairs/TWILIO_VERIFY_SERVICE_SID` | `TWILIO_VERIFY_SERVICE_SID` |
| `/orderly-affairs/CLOUDINARY_CLOUD_NAME` | `CLOUDINARY_CLOUD_NAME` |
| `/orderly-affairs/CLOUDINARY_API_KEY` | `CLOUDINARY_API_KEY` |
| `/orderly-affairs/CLOUDINARY_API_SECRET` | `CLOUDINARY_API_SECRET` |

Startup should show: `applied=17` … `sources=ssm:/orderly-affairs/ (17 params)`.

Keep an **offline copy** of `BACKUP_ENCRYPTION_KEY` (disaster recovery). Local optional:
`storage/BACKUP_ENCRYPTION_KEY.offline.txt` (gitignored under `/storage/`).

## Thin Hostinger `.env` (bootstrap only)

```env
APP_ENV=production
FRONTEND_URL=https://portal.orderly-affairs.com
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
AWS_BUCKET=orderly-affairs-s3-storage

AWS_SECRETS_MANAGER_ENABLED=true
AWS_SSM_PARAMETER_PATH=/orderly-affairs/
AWS_SECRETS_MANAGER_OVERRIDE=true

STRIPE_PRICE_MONTHLY=...
STRIPE_PRICE_YEARLY=...
EMAIL_SENDER=support@orderly-affairs.com
ADMIN_EMAILS=...
VAULT_S3_ENABLED=true
MESSAGE_S3_ENABLED=true
SECTION_S3_ENABLED=true
BACKUP_S3_ENABLED=true
```

Do **not** put AES/JWT/Mongo/Stripe secret/webhook/Twilio/Cloudinary/OpenAI/SendGrid/backup keys in `.env`.

## IAM policy (read)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SsmReadOrderlyAffairs",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParametersByPath",
        "ssm:GetParameters",
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:us-east-1:ACCOUNT_ID:parameter/orderly-affairs/*"
    },
    {
      "Sid": "KmsDecryptSecureString",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "ssm.us-east-1.amazonaws.com"
        }
      }
    }
  ]
}
```

Plus existing S3 permissions for the bucket prefixes.

Optional write (for `scripts/put_ssm_remaining_secrets.py`):

```json
{
  "Sid": "SsmPutOrderlyAffairs",
  "Effect": "Allow",
  "Action": ["ssm:PutParameter", "ssm:AddTagsToResource"],
  "Resource": "arn:aws:ssm:us-east-1:ACCOUNT_ID:parameter/orderly-affairs/*"
}
```

## Hostinger + long-lived AWS keys

Hostinger has no instance role. Mitigate: split SSM-reader vs S3-writer IAM users, rotate keys every 90 days, `chmod 600 .env`, optional `Aws:SourceIp` condition on the VPS IP.

## Production checklist

1. `APP_ENV=production` on VPS (aliases `prod` / `staging` also harden cookies, errors, OpenAPI).
2. Confirm API log: `Loaded secrets from AWS (applied=17, … sources=ssm:/orderly-affairs/ (17 params))`.
3. S3 **Block Public Access** ON (verified for `orderly-affairs-s3-storage`).
4. Offline copy of `BACKUP_ENCRYPTION_KEY`.
5. Rotate AWS access keys if they were ever shared/committed.
6. Account purge fails closed if S3 prefix wipe errors; feedback Mongo rows are deleted.
