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
    */termx-backend|*/node|*/nodejs_wheel/bin/node|*/chrome|*/Chromium|*/chrome-headless-shell|*/Chromium\ Helper*|*/Google\ Chrome\ for\ Testing|*/Google\ Chrome\ for\ Testing\ Helper*) [ -f "$ENTITLEMENTS" ] && echo "$ENTITLEMENTS" || true ;;
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

is_bundle_main() (
  candidate="$1"
  ancestor="$(dirname "$candidate")"
  while [ "$ancestor" != / ] && [ "$ancestor" != . ]; do
    case "$ancestor" in
      *.app)
        executable="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$ancestor/Contents/Info.plist" 2>/dev/null || true)"
        [ "$candidate" = "$ancestor/Contents/MacOS/$executable" ] && exit 0
        ;;
      *.framework)
        base="$(basename "$ancestor" .framework)"
        [ "$(basename "$candidate")" = "$base" ] && exit 0
        ;;
    esac
    ancestor="$(dirname "$ancestor")"
  done
  exit 1
)

sign_all() {
  find "$TARGET" -type f "$@" -print | while IFS= read -r file; do
    if is_macho "$file" && ! is_bundle_main "$file"; then
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
  for component in Resources Headers Modules Helpers Libraries XPCServices; do
    if [ -e "$framework/Versions/$version_dir/$component" ] && [ ! -L "$framework/$component" ]; then
      rm -rf "$framework/$component"
      ln -s "Versions/Current/$component" "$framework/$component"
    fi
  done
}

sign_bundles() {
  # Chromium ships nested helpers/frameworks. Sign children before their containers.
  find "$TARGET" -depth -type d \( -name "*.framework" -o -name "*.app" \) -print | while IFS= read -r bundle; do
    ent=""
    case "$bundle" in
      *.app)
        executable="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$bundle/Contents/Info.plist" 2>/dev/null || true)"
        ent="$(entitlements_for "$bundle/Contents/MacOS/$executable")"
        ;;
    esac
    # Signing an app's main executable signs its enclosing app implicitly.
    # Delay that operation until all nested helpers/frameworks are signed.
    if [ -n "$ent" ] && [ -f "$ent" ]; then
      if [ "$IDENTITY" = "-" ]; then
        "$CODESIGN" --force --entitlements "$ent" --sign - "$bundle"
      else
        "$CODESIGN" --force --entitlements "$ent" --options runtime --timestamp --sign "$IDENTITY" "$bundle"
      fi
    elif [ "$IDENTITY" = "-" ]; then
      "$CODESIGN" --force --preserve-metadata=entitlements --sign - "$bundle"
    else
      "$CODESIGN" --force --preserve-metadata=entitlements --options runtime --timestamp --sign "$IDENTITY" "$bundle"
    fi
    "$CODESIGN" --verify --strict "$bundle"
  done
}

# Normalize PyInstaller framework copies before signing their inner code.
find "$TARGET" -depth -type d -name "*.framework" -print | while IFS= read -r framework; do
  normalize_framework "$framework"
done
sign_all -name "*.so"
sign_all -name "*.dylib"
find "$TARGET" -type f ! -name "*.so" ! -name "*.dylib" -print |
  while IFS= read -r file; do
    if is_macho "$file" && ! is_bundle_main "$file"; then sign_one "$file"; fi
  done
sign_bundles
echo "signed sidecar at $TARGET ($IDENTITY)"
