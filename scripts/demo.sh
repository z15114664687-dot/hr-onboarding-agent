#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

mode="${1:-local}"
if [[ "$mode" == "--docker" || "$mode" == "docker" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required for --docker mode." >&2
    exit 2
  fi
  if [[ ! -f .env ]]; then
    cp .env.example .env
  fi
  exec docker compose up --build
fi

if [[ "$mode" != "local" ]]; then
  echo "Usage: ./scripts/demo.sh [local|--docker]" >&2
  exit 2
fi

python_bin="${PYTHON_BIN:-python3.11}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3.11+ is required. Set PYTHON_BIN to its executable." >&2
  exit 2
fi

if [[ ! -x .venv/bin/python ]]; then
  "$python_bin" -m venv .venv
fi
.venv/bin/python -m pip install --requirement requirements.txt
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "Starting the zero-credential synthetic demo at http://127.0.0.1:8000/ui/hr/workspace"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${APP_PORT:-8000}" --reload
