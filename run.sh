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

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --quiet --upgrade pip
fi
# Keep deps in sync (cheap when already satisfied). requirements.lock pins the
# exact tested versions; requirements.txt holds the human-edited ranges.
./.venv/bin/python -m pip install --quiet -r requirements.lock

if [ "$1" = "--dev" ]; then
  exec ./.venv/bin/python app.py
fi
exec ./.venv/bin/python serve.py
