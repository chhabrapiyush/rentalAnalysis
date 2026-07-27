#!/usr/bin/env bash
# One-shot environment setup (local or cloud/CI). Idempotent.
set -euo pipefail

PY="${PYTHON:-python3.11}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.11 not found. Set PYTHON=/path/to/python3.11 and re-run." >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium

[ -f .env ] || { [ -f .env.example ] && cp .env.example .env && \
  echo "Created .env from .env.example — fill in ONEHOME_* (and SMTP_* for --email-to)."; }

echo "Setup complete. Run tests: .venv/bin/pytest -m 'not integration'"
