#!/usr/bin/env bash
# Regenerate requirements.txt: every dependency OnTask and its tooling use,
# pinned to an exact version with the hashes of the files that version ships,
# for macOS, Windows and Linux in one file.
#
#     ./scripts/update-lock.sh            refresh after editing pyproject.toml
#     ./scripts/update-lock.sh --upgrade  move everything to the newest versions
#
# CI fails when this file is out of step with pyproject.toml.
set -euo pipefail
cd "$(dirname "$0")/.."

UV=${UV:-./.venv/bin/uv}
if [ ! -x "$UV" ] && ! command -v "$UV" >/dev/null; then
    echo "uv is not installed: ./.venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

# --universal resolves for every platform at once (markers say which lines
# apply where); 3.11 is the oldest supported Python, so the pins work on all.
"$UV" pip compile pyproject.toml \
    --extra dev --extra bundle \
    --universal --python-version 3.11 \
    --generate-hashes --quiet \
    --custom-compile-command "./scripts/update-lock.sh" \
    "$@" -o requirements.txt
