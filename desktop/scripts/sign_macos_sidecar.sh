#!/bin/sh
# Sign every Mach-O file inside the staged Python sidecar (inner code first).
# Usage: sign_macos_sidecar.sh <identity|-> <sidecar-dir>
set -eu

IDENTITY="${1:?codesign identity or - for ad-hoc}"
TARGET="${2:?sidecar directory}"
ROOT="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
ENTITLEMENTS="${TERMX_SIDECAR_ENTITLEMENTS:-$ROOT/desktop/src-tauri/entitlements.plist}"
HELPER_ENTITLEMENTS="$ROOT/helpers/macos/signing"

CODESIGN="${CODESIGN:-$(command -v codesign || true)}"
if [ -z "$CODESIGN" ] && [ -x /usr/bin/codesign ]; then
  CODESIGN=/usr/bin/codesign
fi
if [ -z "$CODESIGN" ]; then
  echo "codesign not found" >&2
  exit 1
fi

is_macho() {
  file "$1" | grep -q "Mach-O"
}

entitlements_for() {
  case "$1" in
    */helpers/macos/bin/termx-capture*) echo "$HELPER_ENTITLEMENTS/termx-capture.entitlements" ;;
    */helpers/macos/bin/termx-virtual-display*) echo "$HELPER_ENTITLEMENTS/termx-virtual-display.entitlements" ;;
    */termx-backend) [ -f "$ENTITLEMENTS" ] && echo "$ENTITLEMENTS" || true ;;
    *) true ;;
  esac
}

sign_one() {
  file="$1"
  ent="$(entitlements_for "$file")"
  if [ -n "$ent" ] && [ -f "$ent" ]; then
    if [ "$IDENTITY" = "-" ]; then
      "$CODESIGN" --force --sign - --entitlements "$ent" "$file"
    else
      "$CODESIGN" --force --options runtime --timestamp --entitlements "$ent" "$file"
    fi
  else
    if [ "$IDENTITY" = "-" ]; then
      "$CODESIGN" --force --sign - "$file"
    else
      "$CODESIGN" --force --options runtime --timestamp "$file"
    fi
  fi
}

sign_all() {
  find "$TARGET" -type f "$@" -print | while IFS= read -r file; do
    if is_macho "$file"; then
      sign_one "$file"
    fi
  done
}

# Libraries first, then binaries, then a catch-all for anything unusual.
sign_all -name "*.so"
sign_all -name "*.dylib"
find "$TARGET" -type f ! -name "*.so" ! -name "*.dylib" -print | while IFS= read -r file; do
  if is_macho "$file"; then
    sign_one "$file"
  fi
done
echo "signed sidecar at $TARGET ($IDENTITY)"
