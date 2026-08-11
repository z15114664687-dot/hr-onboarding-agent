#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

python_bin="${PYTHON_BIN:-python3.11}"
if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python 3.11 is required. Set PYTHON_BIN to a compatible interpreter." >&2
  exit 2
fi

version="$($python_bin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$version" != "3.11" && "${ALLOW_COMPATIBLE_PYTHON:-false}" != "true" ]]; then
  echo "Expected Python 3.11, found $version. Set ALLOW_COMPATIBLE_PYTHON=true only for a local compatibility check." >&2
  exit 2
fi

"$python_bin" -m pip check
"$python_bin" -m ruff check app tests scripts
"$python_bin" -m compileall -q app scripts
"$python_bin" -m pytest -q
