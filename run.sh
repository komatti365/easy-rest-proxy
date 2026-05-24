#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Creating virtualenv in $DIR/.venv..."
  python3 -m venv .venv
  source .venv/bin/activate
  python3 -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi

# Load .env into environment if present
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "Starting uvicorn (development)..."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8888
