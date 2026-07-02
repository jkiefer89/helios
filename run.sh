#!/usr/bin/env bash
# Start Helios live on your local network (production server + password gate).
#
#   ./run.sh                       # auto-generates a password, prints it
#   HELIOS_PASSWORD=secret ./run.sh
#   HELIOS_PORT=8080 ./run.sh
#   HELIOS_TLS=1 ./run.sh          # self-signed HTTPS (encrypts the login)
#   ./run.sh --dev                 # localhost-only Flask dev server
set -e
cd "$(dirname "$0")"

# Recreate an unhealthy venv (e.g. broken after a system Python upgrade).
if [ -d ".venv" ] && ! ./.venv/bin/python -c 'import flask' >/dev/null 2>&1; then
  echo "⚠  Existing .venv is unhealthy (python or flask broken) — recreating it…"
  rm -rf .venv
fi
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
fi
# Keep deps in sync (cheap when already satisfied). requirements.lock pins the
# exact tested versions; requirements.txt holds the human-edited ranges.
./.venv/bin/python -m pip install --quiet -r requirements.lock

if [ ! -f "frontend/dist/index.html" ] && ! command -v npm >/dev/null 2>&1; then
  echo "⚠  React frontend is not built (frontend/dist missing) and npm is not installed."
  echo "   Helios will serve build instructions at / until the React UI is built."
  echo "   Install Node.js, then run:  npm --prefix frontend ci && npm --prefix frontend run build"
fi

if [ "$1" = "--dev" ]; then
  exec ./.venv/bin/python app.py
fi
exec ./.venv/bin/python serve.py
