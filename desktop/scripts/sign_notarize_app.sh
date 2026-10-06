#!/bin/sh
# Sign, notarize, staple, and remove download quarantine from a macOS app bundle.
# Usage: desktop/scripts/sign_notarize_app.sh /path/to/Agent\ OS.app
set -eu

usage() {
  echo "Usage: $0 /path/to/App.app" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

app_input="$1"
case "$app_input" in
  *.app) ;;
  *) echo "error: expected an .app bundle: $app_input" >&2; exit 2 ;;
esac
case "$app_input" in
  /*) ;;
  *) app_input="$PWD/$app_input" ;;
esac

app_parent="$(CDPATH= cd "$(dirname "$app_input")" && pwd -P)"
app="$app_parent/$(basename "$app_input")"
if [ -L "$app" ] || [ ! -d "$app/Contents" ] || [ ! -f "$app/Contents/Info.plist" ]; then
  echo "error: not a regular macOS app bundle: $app" >&2
  exit 2
fi

script_dir="$(CDPATH= cd "$(dirname "$0")" && pwd -P)"
signing_env="$script_dir/../signing.env"
if [ ! -r "$signing_env" ]; then
  echo "error: missing $signing_env; configure local signing credentials first" >&2
  exit 1
fi
if ! sh -n "$signing_env"; then
  echo "error: invalid shell syntax in $signing_env" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$signing_env"

: "${APPLE_P12_PATH:?APPLE_P12_PATH is not configured in signing.env}"
: "${APPLE_CERTIFICATE_PASSWORD:?APPLE_CERTIFICATE_PASSWORD is not configured in signing.env}"
: "${APPLE_SIGNING_IDENTITY:?APPLE_SIGNING_IDENTITY is not configured in signing.env}"
: "${APPLE_API_KEY_PATH:?APPLE_API_KEY_PATH is not configured in signing.env}"
: "${APPLE_API_KEY_ID:?APPLE_API_KEY_ID is not configured in signing.env}"
: "${APPLE_API_ISSUER:?APPLE_API_ISSUER is not configured in signing.env}"
case "$APPLE_API_KEY_PATH" in
  "~/"*) APPLE_API_KEY_PATH="$HOME/${APPLE_API_KEY_PATH#\~/}" ;;
esac
if [ ! -f "$APPLE_P12_PATH" ] || [ ! -f "$APPLE_API_KEY_PATH" ]; then
  echo "error: signing certificate or App Store Connect API key file is missing" >&2
  exit 1
fi

if ! openssl pkcs12 -in "$APPLE_P12_PATH" -passin "pass:$APPLE_CERTIFICATE_PASSWORD" -noout >/dev/null 2>&1 &&
   ! openssl pkcs12 -legacy -in "$APPLE_P12_PATH" -passin "pass:$APPLE_CERTIFICATE_PASSWORD" -noout >/dev/null 2>&1; then
  echo "error: could not read the configured Developer ID .p12" >&2
  exit 1
fi

login_keychain="$HOME/Library/Keychains/login.keychain-db"
if ! security find-identity -v -p codesigning "$login_keychain" 2>/dev/null |
  grep -Fq "\"$APPLE_SIGNING_IDENTITY\""; then
  security import "$APPLE_P12_PATH" -k "$login_keychain" \
    -P "$APPLE_CERTIFICATE_PASSWORD" \
    -T /usr/bin/codesign -T /usr/bin/security >/dev/null
fi
if ! security find-identity -v -p codesigning "$login_keychain" 2>/dev/null |
  grep -Fq "\"$APPLE_SIGNING_IDENTITY\""; then
  echo "error: configured Developer ID identity is unavailable in the login keychain" >&2
  exit 1
fi

app_name="$(basename "$app")"
work="$(mktemp -d "$app_parent/.${app_name}.resign.XXXXXX")"
staged_app="$work/$app_name"
backup_app="$work/original.app"
notary_json="$work/notary-result.json"
app_replaced=0

cleanup() {
  exit_status=$?
  trap - EXIT HUP INT TERM
  if [ "$app_replaced" -eq 1 ] && [ -d "$backup_app" ] && [ ! -e "$app" ]; then
    mv "$backup_app" "$app" || echo "error: restore the original app from $backup_app" >&2
  fi
  if [ -n "$work" ] && [ -d "$work" ]; then
    /bin/rm -rf "$work"
  fi
  exit "$exit_status"
}
trap cleanup EXIT HUP INT TERM

echo "==> staging $app"
ditto "$app" "$staged_app"
echo "==> signing with $APPLE_SIGNING_IDENTITY"
helper_root="$staged_app/Contents/Resources/agentos/share/bundles"
if [ -d "$helper_root" ]; then
  find "$helper_root" -type f -exec sh -c '
    set -eu
    identity="$1"
    shift
    for candidate do
      file_kind="$(file -b "$candidate")"
      case "$file_kind" in
        *Mach-O*executable*|*Mach-O*shared*library*|*Mach-O*bundle*)
          echo "  signing $(basename "$candidate")"
          codesign --force --options runtime --timestamp \
            --sign "$identity" "$candidate" </dev/null
          ;;
      esac
    done
  ' sign-helper "$APPLE_SIGNING_IDENTITY" {} +
fi
codesign --force --deep --options runtime --timestamp \
  --sign "$APPLE_SIGNING_IDENTITY" "$staged_app"
codesign --verify --deep --strict --verbose=2 "$staged_app"

zip_path="$work/${app_name%.app}.zip"
ditto -c -k --keepParent "$staged_app" "$zip_path"
echo "==> submitting to Apple for notarization"
if ! xcrun notarytool submit "$zip_path" \
  --key "$APPLE_API_KEY_PATH" \
  --key-id "$APPLE_API_KEY_ID" \
  --issuer "$APPLE_API_ISSUER" \
  --wait --output-format json > "$notary_json"; then
  cat "$notary_json" >&2
  exit 1
fi
notary_status="$(/usr/bin/plutil -extract status raw -o - "$notary_json" 2>/dev/null || true)"
if [ "$notary_status" != "Accepted" ]; then
  cat "$notary_json" >&2
  submission_id="$(/usr/bin/plutil -extract id raw -o - "$notary_json" 2>/dev/null || true)"
  if [ -n "$submission_id" ]; then
    xcrun notarytool log "$submission_id" \
      --key "$APPLE_API_KEY_PATH" \
      --key-id "$APPLE_API_KEY_ID" \
      --issuer "$APPLE_API_ISSUER" >&2 || true
  fi
  echo "error: Apple did not accept this app for notarization" >&2
  exit 1
fi

echo "==> stapling and validating Apple's ticket"
xcrun stapler staple "$staged_app"
xcrun stapler validate "$staged_app"
spctl --assess --type execute --verbose=2 "$staged_app"

if xattr -lr "$staged_app" 2>/dev/null | grep -q 'com.apple.quarantine'; then
  echo "==> clearing download quarantine from the notarized app"
  xattr -dr com.apple.quarantine "$staged_app"
fi

echo "==> replacing the supplied app with the notarized copy"
mv "$app" "$backup_app"
app_replaced=1
mv "$staged_app" "$app"
app_replaced=0
codesign --verify --deep --strict --verbose=2 "$app"
xcrun stapler validate "$app"
spctl --assess --type execute --verbose=2 "$app"
echo "==> ready to open: $app"
