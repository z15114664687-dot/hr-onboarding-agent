#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

python_bin="${PYTHON_BIN:-python3.11}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3.11+ is required. Set PYTHON_BIN to its executable." >&2
  exit 2
fi

if [[ ! -x .venv-demo/bin/python ]]; then
  "$python_bin" -m venv .venv-demo
fi
.venv-demo/bin/python -m pip install --requirement requirements-demo.txt
.venv-demo/bin/python -m playwright install chromium
exec .venv-demo/bin/python scripts/capture_demo.py "$@"
