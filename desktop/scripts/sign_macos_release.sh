#!/bin/sh
# Sign, notarize, and staple Termx macOS artifacts — no arguments required.
#
#   desktop/scripts/sign_macos_release.sh
#
# Discovers the newest .dmg (or .app) from the local build output, ./dist, the current
# directory, and ~/Downloads. Use --all to sign everything it finds.
#
# Configuration is read from the environment and from desktop/signing.env
# (copy desktop/signing.env.example and edit):
#
#   APPLE_SIGNING_IDENTITY="Developer ID Application: Name (TEAMID)"
#   NOTARY_PROFILE=termx-notary            # or APPLE_ID + APPLE_TEAM_ID + APPLE_PASSWORD
#   APPLE_KEYCHAIN=                        # optional
#
# With no identity the script ad-hoc signs, which is enough to run the app on this Mac.
# With no notarization credentials it signs and prints the exact commands to run later.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
ENTITLEMENTS="$REPO_ROOT/desktop/src-tauri/entitlements.plist"

# shellcheck disable=SC1090
if [ -f "$REPO_ROOT/desktop/signing.env" ]; then
  set -a
  . "$REPO_ROOT/desktop/signing.env"
  set +a
fi

IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
KEYCHAIN="${APPLE_KEYCHAIN:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
IN_PLACE=0
ALL=0
DRY_RUN=0
INPUTS=""
CURRENT_WORK=""
CURRENT_MOUNT=""

cleanup() {
  if [ -n "$CURRENT_MOUNT" ]; then
    hdiutil detach "$CURRENT_MOUNT" -quiet >/dev/null 2>&1 || true
    CURRENT_MOUNT=""
  fi
  if [ -n "$CURRENT_WORK" ]; then
    rm -rf "$CURRENT_WORK"
    CURRENT_WORK=""
  fi
}
trap cleanup EXIT INT TERM

usage() {
  cat <<'EOF'
usage: sign_macos_release.sh [options] [artifact...]

With no artifact it signs the newest .dmg/.app found in:
  desktop/src-tauri/target/release/bundle/{dmg,macos}, ./dist, ., ~/Downloads

options:
  --all                 sign every discovered artifact
  --input PATH          search PATH (file or directory); repeatable
  --identity NAME       codesign identity (default: APPLE_SIGNING_IDENTITY or "-")
  --keychain PATH       keychain containing the identity
  --notary-profile NAME notarytool keychain profile
  --in-place            replace the input instead of writing signed-<name>
  --dry-run             print what would be signed and exit
  -h, --help            show this help

environment / desktop/signing.env:
  APPLE_SIGNING_IDENTITY, APPLE_KEYCHAIN
  NOTARY_PROFILE  or  APPLE_ID + APPLE_TEAM_ID + APPLE_PASSWORD
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --all) ALL=1; shift ;;
    --input) INPUTS="$INPUTS$2
"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --keychain) KEYCHAIN="$2"; shift 2 ;;
    --notary-profile) NOTARY_PROFILE="$2"; shift 2 ;;
    --in-place) IN_PLACE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "error: unknown option $1" >&2; usage >&2; exit 1 ;;
    *) INPUTS="$INPUTS$1
"; shift ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: macOS signing/notarization requires a Mac" >&2
  exit 1
fi

newest() {
  [ -n "$1" ] || return 1
  printf '%s\n' "$1" | while IFS= read -r candidate; do
    [ -n "$candidate" ] && printf '%s\t%s\n' "$(stat -f '%m' "$candidate" 2>/dev/null || echo 0)" "$candidate"
  done | sort -rn | head -n 1 | cut -f2-
}

collect_from() {
  root="$1"
  if [ -f "$root" ]; then
    case "$root" in
      *.dmg) printf '%s\n' "$root" ;;
      *.app) printf '%s\n' "$root" ;;
    esac
    return 0
  fi
  [ -d "$root" ] || return 0
  find "$root" -maxdepth 2 -type f -name '*.dmg' 2>/dev/null || true
  find "$root" -maxdepth 1 -type d -name '*.app' 2>/dev/null || true
}

discover() {
  if [ -n "$INPUTS" ]; then
    roots="$INPUTS"
  elif [ -n "${TERMX_SIGN_INPUT:-}" ]; then
    roots="$TERMX_SIGN_INPUT"
  else
    roots="$REPO_ROOT/desktop/src-tauri/target/release/bundle/dmg
$REPO_ROOT/desktop/src-tauri/target/release/bundle/macos
$REPO_ROOT/dist
$PWD
$HOME/Downloads"
  fi
  printf '%s\n' "$roots" | while IFS= read -r root; do
    [ -n "$root" ] && collect_from "$root"
  done | awk 'NF && !seen[$0]++' | grep -v '/signed-[^/]*$' || true
}

ARTIFACTS="$(discover)"
DMGS="$(printf '%s\n' "$ARTIFACTS" | grep '\.dmg$' || true)"
APPS="$(printf '%s\n' "$ARTIFACTS" | grep '\.app$' || true)"

if [ "$ALL" = "1" ]; then
  SELECTED="$ARTIFACTS"
elif [ -n "$DMGS" ]; then
  SELECTED="$(newest "$DMGS")"
else
  SELECTED="$(newest "$APPS")"
fi

if [ -z "${SELECTED:-}" ]; then
  echo "error: no .dmg or .app found." >&2
  echo "hint: pass a path, set TERMX_SIGN_INPUT=/path, or build first:" >&2
  echo "      cargo tauri build --bundles dmg   (in desktop/src-tauri)" >&2
  exit 1
fi

SELECT_FILE="$(mktemp "${TMPDIR:-/tmp}/termx-sign-list.XXXXXX")"
printf '%s\n' "$SELECTED" > "$SELECT_FILE"

echo "==> artifacts to sign:"
while IFS= read -r item; do
  [ -n "$item" ] && echo "  $item"
done < "$SELECT_FILE"

if [ "$DRY_RUN" = "1" ]; then
  rm -f "$SELECT_FILE"
  echo "==> dry run; identity would be: $IDENTITY"
  exit 0
fi

codesign_extra() {
  if [ -n "$KEYCHAIN" ]; then
    echo "--keychain $KEYCHAIN"
  fi
}

sign_app() {
  app="$1"
  backend="$app/Contents/Resources/backend"
  if [ -d "$backend" ]; then
    sh "$SCRIPT_DIR/sign_macos_sidecar.sh" "$IDENTITY" "$backend"
  fi
  # shellcheck disable=SC2086
  if [ "$IDENTITY" = "-" ]; then
    codesign --force --options runtime $(codesign_extra) --sign - "$app"
  else
    # shellcheck disable=SC2086
    codesign --force --options runtime --timestamp $(codesign_extra) \
      --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$app"
  fi
  codesign --verify --deep --strict --verbose=2 "$app"
}

notary_args() {
  if [ -n "$NOTARY_PROFILE" ]; then
    echo "--keychain-profile $NOTARY_PROFILE"
    return 0
  fi
  if [ -n "${APPLE_ID:-}" ]; then
    : "${APPLE_TEAM_ID:?APPLE_TEAM_ID is required together with APPLE_ID}"
    : "${APPLE_PASSWORD:?APPLE_PASSWORD (app-specific) is required together with APPLE_ID}"
    echo "--apple-id $APPLE_ID --team-id $APPLE_TEAM_ID --password $APPLE_PASSWORD"
    return 0
  fi
  return 1
}

notarize_dmg() {
  # shellcheck disable=SC2046
  if ! args="$(notary_args)"; then
    echo "note: no notarization credentials configured; skipping notarization."
    echo "      later: xcrun notarytool submit \"$1\" --keychain-profile <profile> --wait"
    echo "             xcrun stapler staple \"$1\""
    return 1
  fi
  # shellcheck disable=SC2086
  xcrun notarytool submit "$1" $args --wait
  xcrun stapler staple "$1"
  xcrun stapler validate "$1"
  return 0
}

notarize_app() {
  zip_path="$1"
  app="$2"
  # shellcheck disable=SC2046
  if ! args="$(notary_args)"; then
    echo "note: no notarization credentials configured; skipping notarization."
    echo "      later: ditto -c -k --keepParent \"$app\" \"$app.zip\""
    echo "             xcrun notarytool submit \"$app.zip\" --keychain-profile <profile> --wait"
    echo "             xcrun stapler staple \"$app\""
    return 1
  fi
  # shellcheck disable=SC2086
  xcrun notarytool submit "$zip_path" $args --wait
  xcrun stapler staple "$app"
  xcrun stapler validate "$app"
  return 0
}

process_app() {
  source_app="$1"
  work="$(mktemp -d "${TMPDIR:-/tmp}/termx-sign.XXXXXX")"
  CURRENT_WORK="$work"
  name="$(basename "$source_app")"
  cp -R "$source_app" "$work/$name"
  app="$work/$name"
  echo "==> signing $name"
  sign_app "$app"
  zip_path="$work/${name%.app}.zip"
  ditto -c -k --keepParent "$app" "$zip_path"
  if notarize_app "$zip_path" "$app"; then
    if [ "$IDENTITY" != "-" ]; then
      spctl --assess --type execute --verbose=2 "$app" || true
    fi
  fi
  if [ "$IN_PLACE" = "1" ]; then
    rm -rf "$source_app"
    cp -R "$app" "$source_app"
    destination="$source_app"
  else
    destination="$(dirname "$source_app")/signed-$name"
    rm -rf "$destination"
    cp -R "$app" "$destination"
  fi
  echo "==> ready: $destination"
}

process_dmg() {
  source_dmg="$1"
  work="$(mktemp -d "${TMPDIR:-/tmp}/termx-sign.XXXXXX")"
  CURRENT_WORK="$work"
  mount="$work/mnt"
  CURRENT_MOUNT="$mount"
  mkdir -p "$mount"
  hdiutil attach "$source_dmg" -nobrowse -readonly -mountpoint "$mount" >/dev/null
  app_src="$(find "$mount" -maxdepth 1 -name '*.app' | head -n 1)"
  if [ -z "$app_src" ]; then
    echo "error: no .app found inside $source_dmg" >&2
    exit 1
  fi
  name="$(basename "$app_src")"
  cp -R "$app_src" "$work/$name"
  hdiutil detach "$mount" -quiet >/dev/null
  CURRENT_MOUNT=""
  app="$work/$name"
  echo "==> signing $name"
  sign_app "$app"
  original="$(basename "$source_dmg")"
  output="$work/$original"
  hdiutil create -volname "${name%.app}" -srcfolder "$app" -ov -format UDZO "$output" >/dev/null
  if [ "$IDENTITY" != "-" ]; then
    # shellcheck disable=SC2086
    codesign --force --timestamp $(codesign_extra) --sign "$IDENTITY" "$output"
    codesign --verify --verbose=2 "$output"
  fi
  notarize_dmg "$output" || true
  if [ "$IN_PLACE" = "1" ]; then
    destination="$source_dmg"
  else
    destination="$(dirname "$source_dmg")/signed-$original"
  fi
  cp "$output" "$destination"
  echo "==> ready: $destination"
  if [ "$IDENTITY" = "-" ]; then
    echo "note: ad-hoc signature only. On another Mac run:"
    echo "      xattr -dr com.apple.quarantine /Applications/Termx.app"
    echo "      (or right-click the app and choose Open the first time)"
  fi
}

while IFS= read -r target; do
  [ -n "$target" ] || continue
  CURRENT_WORK=""
  CURRENT_MOUNT=""
  case "$target" in
    *.app) process_app "$target" ;;
    *.dmg) process_dmg "$target" ;;
  esac
done < "$SELECT_FILE"
rm -f "$SELECT_FILE"

echo
echo "done. identity: $IDENTITY${NOTARY_PROFILE:+ | notary profile: $NOTARY_PROFILE}"
