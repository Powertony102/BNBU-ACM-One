#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_BIN="${CONDA_BIN:-/Users/lixinze/opt/anaconda3/bin/conda}"
CONDA_ENV="${CONDA_ENV:-asdw}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

# Resend configuration.
# Override these before startup for real email delivery.
export RESEND_API_KEY="${RESEND_API_KEY:-re_87BBQNbh_EmhjQryJD6S4ruD2hsC4UHu2}"
export DEFAULT_FROM_EMAIL="${DEFAULT_FROM_EMAIL:-no-reply@mail.tony102.com}"

if [[ ! -x "${CONDA_BIN}" ]]; then
  echo "Conda binary not found at ${CONDA_BIN}" >&2
  exit 1
fi

if [[ "${RESEND_API_KEY}" == "re_xxx" ]]; then
  echo "Warning: RESEND_API_KEY is still set to the placeholder value 're_xxx'." >&2
  echo "Real email delivery will not work until you export a valid key." >&2
fi

if [[ "${DEFAULT_FROM_EMAIL}" == "no-reply@example.com" ]]; then
  echo "Warning: DEFAULT_FROM_EMAIL is still using the placeholder address." >&2
  echo "Please replace it with an address from your verified Resend domain." >&2
fi

cd "${ROOT_DIR}"

echo "==> Applying database migrations..."
"${CONDA_BIN}" run -n "${CONDA_ENV}" python manage.py migrate

echo "==> Bootstrapping demo data..."
"${CONDA_BIN}" run -n "${CONDA_ENV}" python manage.py bootstrap_demo

echo "==> Starting development server at http://${HOST}:${PORT}"
echo "    Super Admin: superadmin / ACM123456"
echo "    Member: member01 / ACM123456"
echo "    From email: ${DEFAULT_FROM_EMAIL}"

exec "${CONDA_BIN}" run -n "${CONDA_ENV}" python manage.py runserver "${HOST}:${PORT}"
