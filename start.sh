#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "Virtualenv not found. Run ./run.sh first to create and install dependencies."
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8888
