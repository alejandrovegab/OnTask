#!/usr/bin/env bash
# Convenience launcher: creates the venv on first run, then starts OnTask.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtualenv..."
    python3 -m venv .venv
    ./.venv/bin/pip install -q --upgrade pip
    ./.venv/bin/pip install -q -r requirements.txt
fi

exec ./.venv/bin/python -m ontask "$@"
