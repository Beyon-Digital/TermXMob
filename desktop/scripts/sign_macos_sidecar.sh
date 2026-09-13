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
  find "$TARGET" -type f "$@" ! -path "*.framework/*" -print | while IFS= read -r file; do
    if is_macho "$file"; then
      sign_one "$file"
    fi
  done
}

normalize_framework() {
  # Tauri's resource copy flattens the framework symlinks PyInstaller creates,
  # which makes codesign report "bundle format is ambiguous". Rebuild the
  # standard versioned layout before signing.
  framework="$1"
  base="$(basename "$framework" .framework)"
  [ -d "$framework/Versions" ] || return 0
  version_dir=""
  if [ -L "$framework/Versions/Current" ]; then
    version_dir="$(readlink "$framework/Versions/Current")"
  else
    for candidate in "$framework"/Versions/*; do
      [ -d "$candidate" ] || continue
      case "$(basename "$candidate")" in
        Current) continue ;;
      esac
      version_dir="$(basename "$candidate")"
      break
    done
  fi
  [ -n "$version_dir" ] || return 0
  if [ ! -L "$framework/Versions/Current" ]; then
    rm -rf "$framework/Versions/Current"
    ln -s "$version_dir" "$framework/Versions/Current"
  fi
  if [ ! -L "$framework/$base" ]; then
    rm -f "$framework/$base"
    ln -s "Versions/Current/$base" "$framework/$base"
  fi
  if [ -d "$framework/Resources" ] && [ ! -L "$framework/Resources" ]; then
    rm -rf "$framework/Resources"
    ln -s "Versions/Current/Resources" "$framework/Resources"
  fi
}

sign_frameworks() {
  find "$TARGET" -type d -name "*.framework" -print | while IFS= read -r framework; do
    normalize_framework "$framework"
    if [ "$IDENTITY" = "-" ]; then
      "$CODESIGN" --force --sign - "$framework"
    else
      "$CODESIGN" --force --options runtime --timestamp --sign "$IDENTITY" "$framework"
    fi
    "$CODESIGN" --verify --strict "$framework"
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
