#!/usr/bin/env bash
# Build dist/OnTask.app: a py2app bundle with a launcher compiled against this
# Mac's SDK, signed with a stable identity so macOS keeps its permissions across
# rebuilds.
#
#     ./scripts/build-mac-app.sh
#
# Signs with the "OnTask Local Signing" certificate (see docs/maintaining.md),
# or the identity in $ONTASK_SIGNING_IDENTITY. Without one it signs ad hoc, which
# runs fine but makes macOS forget OnTask's permissions on every rebuild.
set -euo pipefail
cd "$(dirname "$0")/.."

IDENTITY="${ONTASK_SIGNING_IDENTITY:-OnTask Local Signing}"
MIN_MACOS=15.0
PY=./.venv/bin/python
APP=dist/OnTask.app

if [ ! -x "$PY" ]; then
    echo "No virtualenv yet: run ./run.sh once first." >&2
    exit 1
fi

echo "==> Installing pinned dependencies"
"$PY" -m pip install -q --require-hashes -r requirements.txt
"$PY" -m pip install -q --no-deps -e .

echo "==> Building the bundle with py2app"
rm -rf build dist
root=$(pwd)
log=$(mktemp)
# Run from packaging/macos: from the project root, setuptools would also read
# pyproject.toml and hand py2app the dependency list, which it rejects.
if ! (cd packaging/macos && "$root/$PY" setup_app.py py2app \
        --dist-dir "$root/dist" --bdist-base "$root/build") >"$log" 2>&1; then
    tail -40 "$log" >&2
    echo "py2app failed; full log: $log" >&2
    exit 1
fi
rm -f "$log"

# py2app ships a prebuilt launcher linked against an old SDK, and macOS picks
# an app's look (Liquid Glass or the compatibility look) from the SDK its main
# executable was built with. Rebuild the launcher from py2app's own source.
echo "==> Building the launcher against macOS SDK $(xcrun --show-sdk-version)"
template=$("$PY" -c 'import os, py2app; print(os.path.join(os.path.dirname(py2app.__file__), "apptemplate", "src", "main.c"))')
xcrun clang -O2 -arch arm64 -arch x86_64 -mmacosx-version-min="$MIN_MACOS" \
    -Wno-deprecated-declarations -framework Cocoa \
    -o "$APP/Contents/MacOS/OnTask" "$template"

if security find-identity -v -p codesigning | grep -qF "\"$IDENTITY\""; then
    sign="$IDENTITY"
    echo "==> Signing as \"$IDENTITY\""
else
    sign=-
    echo "==> Signing ad hoc: no \"$IDENTITY\" certificate found." >&2
    echo "    macOS will ask for OnTask's permissions again after every rebuild." >&2
fi

# Inside out: every loadable binary, then the frameworks, then the bundle.
codesign_quiet() { codesign --force --sign "$sign" --timestamp=none "$@" 2>&1 | grep -v "replacing existing signature" || true; }
find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 |
    while IFS= read -r -d '' binary; do codesign_quiet "$binary"; done
for framework in "$APP"/Contents/Frameworks/*.framework; do
    [ -e "$framework" ] && codesign_quiet "$framework"
done
codesign_quiet "$APP/Contents/MacOS/python"
codesign_quiet "$APP"
codesign --verify --strict --deep "$APP"

echo
echo "Built $APP ($(du -sh "$APP" | cut -f1)). Open it with:"
echo "    open $APP"
