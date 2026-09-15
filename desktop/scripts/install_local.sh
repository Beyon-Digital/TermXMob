#!/bin/sh
# Install a signed Termx.app build without corrupting the bundle.
#
# `ditto` merges into an existing destination, so installing over a running or
# stale copy leaves files behind that are not in the new code signature. macOS
# then reports "a sealed resource is missing or invalid" and refuses to apply
# the app's privacy (TCC) grants, which breaks screen recording and input.
#
# Usage: install_local.sh [path/to/signed-Termx.app] [destination]

set -eu

SOURCE="${1:-desktop/src-tauri/target/release/bundle/macos/signed-Termx.app}"
DEST="${2:-/Applications/Termx.app}"

if [ ! -d "$SOURCE" ]; then
    echo "error: $SOURCE not found" >&2
    exit 1
fi

echo "==> verifying the source bundle"
codesign --verify --deep --strict "$SOURCE"

echo "==> quitting running instances"
for name in termx-desktop termx-backend Termx; do
    pkill -x "$name" 2>/dev/null || true
done
for _ in $(seq 1 20); do
    if ! pgrep -x termx-desktop >/dev/null 2>&1 && ! pgrep -x termx-backend >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

echo "==> replacing $DEST"
rm -rf "$DEST"
ditto "$SOURCE" "$DEST"

# LaunchServices would otherwise be free to launch the build-tree copy (which
# looks newer), and a rebuilt binary is a new TCC identity: grants made for the
# installed app would silently stop applying. Remove the staging bundle so only
# the installed app exists.
STAGE_DIR="$(dirname "$SOURCE")"
for stale in "$STAGE_DIR/Termx.app" "$STAGE_DIR/signed-Termx.app"; do
    if [ "$stale" != "$DEST" ] && [ -d "$stale" ]; then
        rm -rf "$stale"
    fi
done

echo "==> verifying the installed bundle"
codesign --verify --deep --strict "$DEST"

echo "==> installed version: $(defaults read "$DEST/Contents/Info.plist" CFBundleShortVersionString)"
echo "done."
