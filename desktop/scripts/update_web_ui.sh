#!/bin/sh
# Build the dedicated desktop/browser workspace. Expo remains the mobile client.
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="$REPO_ROOT/desktop/workspace"
command -v pnpm >/dev/null 2>&1 || { echo 'error: pnpm 10 is required' >&2; exit 1; }
pnpm --dir "$WORKSPACE" install --frozen-lockfile
pnpm --dir "$WORKSPACE" test
pnpm --dir "$WORKSPACE" build
# This directory is generated and ignored; preserve source and the Expo checkout.
rm -rf "$REPO_ROOT/desktop/web"
cp -R "$WORKSPACE/dist" "$REPO_ROOT/desktop/web"
echo 'updated desktop/web from the dedicated workspace'
