#!/usr/bin/env bash
# Start the web interface. Installs node modules on first run.
#
#   ./run.sh     # http://localhost:5173  (proxies /api -> backend :8000)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  echo "==> Installing frontend dependencies (first run only)"
  npm install --no-fund --no-audit
fi

echo "==> Interface on http://localhost:5173"
echo "==> Expecting the backend at ${API_TARGET:-http://localhost:8000}"
exec npm run dev
