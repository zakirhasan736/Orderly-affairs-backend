# Change management

Production changes go through git pull requests. No direct edits on Hostinger except emergency hotfix, which is committed within 24 hours.

Required on each PR: what changed, risk (low/med/high), how tested, secrets not committed. See `.github/pull_request_template.md`.

High-risk (auth, crypto, privacy, backups): second reviewer before merge. Deploy only after `python scripts/verify_soc2_type1.py` on the target environment.

Rollback: previous git tag / prior Hostinger release.
