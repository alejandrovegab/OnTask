#!/usr/bin/env bash
# Convenience launcher: creates the venv on first run, keeps OnTask installed in
# it whenever pyproject.toml changes, then starts the app.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtualenv..."
    python3 -m venv .venv
    ./.venv/bin/pip install -q --upgrade pip
fi

# Dependencies come from the lock (exact versions, hash-checked); OnTask itself
# is then installed editable, since the package lives under src/. Redone when
# either file changes.
stamp=.venv/.ontask-installed
if [ ! -f "$stamp" ] || [ pyproject.toml -nt "$stamp" ] || [ requirements.txt -nt "$stamp" ]; then
    echo "Installing OnTask into the virtualenv..."
    ./.venv/bin/pip install -q --require-hashes -r requirements.txt
    ./.venv/bin/pip install -q --no-deps -e .
    touch "$stamp"
fi

exec ./.venv/bin/python -m ontask "$@"
