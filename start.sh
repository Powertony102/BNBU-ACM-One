#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [[ -f ".venv/bin/activate" ]]; then
  # Reuse the project's local virtual environment when it exists.
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: Python is not installed."
  exit 1
fi

if ! "$PYTHON_BIN" -c "import django" >/dev/null 2>&1; then
  echo "Error: Django is not installed in the current Python environment."
  echo "Tip: activate your virtual environment first, or install dependencies before running start.sh."
  exit 1
fi

echo "==> Applying database migrations..."
"$PYTHON_BIN" manage.py migrate

echo "==> Bootstrapping demo data..."
"$PYTHON_BIN" manage.py bootstrap_demo

echo "==> Starting development server at http://$HOST:$PORT"
echo "    Super Admin: superadmin / ACM123456"
echo "    Member: member01 / ACM123456"

exec "$PYTHON_BIN" manage.py runserver "$HOST:$PORT"
