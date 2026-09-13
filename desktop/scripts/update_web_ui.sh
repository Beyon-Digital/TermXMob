#!/bin/sh
# Refresh desktop/web from the private client repository.
#
#   desktop/scripts/update_web_ui.sh
#
# The client source lives in a separate private repo (mobile + web). This script
# clones or pulls it, builds the static web export, and replaces desktop/web.
# Override the checkout location with TERMX_APP_REPO=/path/to/termx-app.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
APP_REPO="${TERMX_APP_REPO:-$HOME/Documents/GitHub/termx-app}"
APP_REMOTE="${TERMX_APP_REMOTE:-Psyborgs-git/termx-app}"

if [ ! -d "$APP_REPO/.git" ]; then
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh repo clone "$APP_REMOTE" "$APP_REPO"
  else
    echo "error: clone $APP_REMOTE to $APP_REPO or set TERMX_APP_REPO" >&2
    exit 1
  fi
else
  git -C "$APP_REPO" pull --ff-only
fi

pnpm="$(command -v pnpm || true)"
if [ -z "$pnpm" ]; then
  echo "error: pnpm is required (npm i -g pnpm@10)" >&2
  exit 1
fi

pnpm --dir "$APP_REPO" install --frozen-lockfile
pnpm --dir "$APP_REPO" export:web

rm -rf "$REPO_ROOT/desktop/web"
cp -R "$APP_REPO/dist" "$REPO_ROOT/desktop/web"
echo "updated desktop/web from $(git -C "$APP_REPO" rev-parse --short HEAD)"
