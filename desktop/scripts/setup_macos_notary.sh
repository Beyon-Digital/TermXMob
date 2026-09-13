#!/bin/sh
# Store notarization credentials in your login keychain so
# desktop/scripts/sign_macos_release.sh can notarize locally — nothing is sent to
# GitHub and no Apple ID/password ever enters the repository or CI secrets.
#
# Two ways to authenticate with Apple:
#
# 1. App Store Connect API key (recommended: scoped, revocable, no password)
#    appstoreconnect.apple.com -> Users and Access -> Integrations -> Team Keys
#    -> Generate API Key (Developer or Admin role), download the .p8 once.
#
#    desktop/scripts/setup_macos_notary.sh \
#      --key ~/Downloads/AuthKey_ABC123XYZ.p8 \
#      --key-id ABC123XYZ \
#      --issuer 00000000-0000-0000-0000-000000000000
#
# 2. Apple ID with an app-specific password (entered interactively here only):
#
#    desktop/scripts/setup_macos_notary.sh
#    # prompts for Apple ID, team id, and an app-specific password from
#    # appleid.apple.com -> Sign-In and Security -> App-Specific Passwords
#
# The result is a notarytool keychain profile (default: termx-notary). The signing
# script picks it up when desktop/signing.env contains:
#     NOTARY_PROFILE=termx-notary
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

PROFILE="${NOTARY_PROFILE:-termx-notary}"
KEY_PATH=""
KEY_ID=""
ISSUER=""

usage() {
  cat <<'EOF'
usage: setup_macos_notary.sh [options]

options:
  --profile NAME   keychain profile name to create (default: termx-notary)
  --key PATH       App Store Connect API private key (.p8), downloaded once
  --key-id ID      API key id (10 characters)
  --issuer ID      API issuer id (UUID from the Integrations page)
  -h, --help       show this help

With no --key the script prompts interactively for Apple ID + app-specific
password (stored only in your login keychain).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --key) KEY_PATH="$2"; shift 2 ;;
    --key-id) KEY_ID="$2"; shift 2 ;;
    --issuer) ISSUER="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: notarization credentials require a Mac" >&2
  exit 1
fi
if ! xcrun --find notarytool >/dev/null 2>&1; then
  echo "error: notarytool not found; install Xcode or Command Line Tools" >&2
  exit 1
fi

case "$KEY_PATH" in
  "~/"*) KEY_PATH="$HOME/${KEY_PATH#\~/}" ;;
esac

if [ -n "$KEY_PATH" ]; then
  if [ ! -f "$KEY_PATH" ]; then
    echo "error: API key not found: $KEY_PATH" >&2
    exit 1
  fi
  if [ -z "$KEY_ID" ] || [ -z "$ISSUER" ]; then
    echo "error: --key-id and --issuer are required with --key" >&2
    usage >&2
    exit 1
  fi
  echo "==> storing API key credentials as notary profile '$PROFILE'"
  xcrun notarytool store-credentials "$PROFILE" \
    --key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER"
else
  echo "==> enter your Apple ID, team id, and an app-specific password"
  echo "    (create one at appleid.apple.com -> Sign-In and Security -> App-Specific Passwords)"
  xcrun notarytool store-credentials "$PROFILE"
fi

echo
echo "==> verifying profile '$PROFILE'"
xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null
echo "profile is valid"

if [ -f "$REPO_ROOT/desktop/signing.env" ]; then
  if ! grep -q "^NOTARY_PROFILE=" "$REPO_ROOT/desktop/signing.env"; then
    printf '\nNOTARY_PROFILE=%s\n' "$PROFILE" >> "$REPO_ROOT/desktop/signing.env"
    echo "added NOTARY_PROFILE=$PROFILE to desktop/signing.env"
  fi
else
  echo "hint: desktop/scripts/sign_macos_release.sh uses NOTARY_PROFILE=$PROFILE"
  echo "      export it or put 'NOTARY_PROFILE=$PROFILE' in desktop/signing.env"
fi

echo
echo "Next: desktop/scripts/sign_macos_release.sh"
