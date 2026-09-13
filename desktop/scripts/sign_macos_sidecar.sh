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
      "$CODESIGN" --force --options runtime --timestamp \
        --entitlements "$ent" --sign "$IDENTITY" "$file"
    fi
  else
    if [ "$IDENTITY" = "-" ]; then
      "$CODESIGN" --force --sign - "$file"
    else
      "$CODESIGN" --force --options runtime --timestamp --sign "$IDENTITY" "$file"
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

sign_ambiguous() {
  # codesign cannot classify "<name>.framework/<name>" in PyInstaller's shallow
  # framework layout. Sign through a hardlink outside the .framework path; the
  # signature is stored in the file itself.
  file="$1"
  linkdir="$TARGET/.termx-sign-links"
  mkdir -p "$linkdir"
  link="$linkdir/$(basename "$file")"
  rm -f "$link"
  if ln "$file" "$link" 2>/dev/null; then
    sign_one "$link"
    rm -f "$link"
  else
    echo "warning: could not hardlink $file; signing it in place" >&2
    sign_one "$file"
  fi
}

sign_frameworks() {
  find "$TARGET" -type d -name "*.framework" -print | while IFS= read -r framework; do
    find "$framework" -type f -print | while IFS= read -r file; do
      is_macho "$file" || continue
      case "$file" in
        "$framework"/*/*) sign_one "$file" ;;
        *) sign_ambiguous "$file" ;;
      esac
    done
  done
}

# Libraries first, then frameworks, then a catch-all for anything unusual.
sign_all -name "*.so"
sign_all -name "*.dylib"
sign_frameworks
find "$TARGET" -type f ! -name "*.so" ! -name "*.dylib" ! -path "*.framework/*" -print |
  while IFS= read -r file; do
    if is_macho "$file"; then
      sign_one "$file"
    fi
  done
echo "signed sidecar at $TARGET ($IDENTITY)"
