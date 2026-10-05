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

# The package lives under src/, so it has to be installed (editable) to be
# importable. Reinstall when pyproject.toml changes: new dependencies, layout.
stamp=.venv/.ontask-installed
if [ ! -f "$stamp" ] || [ pyproject.toml -nt "$stamp" ]; then
    echo "Installing OnTask into the virtualenv..."
    ./.venv/bin/pip install -q -e .
    touch "$stamp"
fi

exec ./.venv/bin/python -m ontask "$@"
