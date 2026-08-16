#!/usr/bin/env bash
# PM2 entrypoint. Do not put a space in app.main:app.
set -euo pipefail
cd /var/www/backend
# Prefer the venv next to this repo; fall back to PATH uvicorn.
if [[ -x /var/www/backend/venv/bin/uvicorn ]]; then
  exec /var/www/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
