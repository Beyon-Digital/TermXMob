#!/bin/sh
# End-to-end GitHub signing setup for Termx — run on your Mac.
#
# You provide two files downloaded from Apple (nothing is pasted into chat):
#   1. Developer ID Application certificate  (developer.apple.com -> Certificates -> +)
#   2. App Store Connect API key             (appstoreconnect.apple.com -> Integrations)
#
# This script then:
#   - validates the certificate is a Developer ID Application cert and matches your key
#   - builds a macOS-compatible .p12 (legacy encryption)
#   - imports it into your login keychain (codesign identity)
#   - writes desktop/signing.env (gitignored)
#   - optionally signs/notarizes the installed app
#   - uploads every signing secret to GitHub with gh
#   - optionally triggers the Desktop release workflow
#
# Usage:
#   desktop/scripts/setup_github_signing.sh \
#     --cer ~/certs/developerid_application.cer \
#     --key ~/certs/termx-developer-id.key \
#     --api-key ~/Downloads/AuthKey_ABC123XYZ.p8 \
#     --api-key-id ABC123XYZ \
#     --api-issuer 00000000-0000-0000-0000-000000000000 \
#     --sign-app /Applications/Termx.app \
#     --tag v0.1.0
#
# Preview everything without changing anything:
#   ... --dry-run
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$REPO_ROOT/desktop/signing.env"

# shellcheck disable=SC1090
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

CER="${APPLE_CER_PATH:-}"
KEY="${APPLE_PRIVATE_KEY_PATH:-$HOME/certs/termx-developer-id.key}"
P12_OUT="${APPLE_P12_PATH:-$HOME/certs/DeveloperID.p12}"
API_KEY="${APPLE_API_KEY_PATH:-}"
API_KEY_ID="${APPLE_API_KEY_ID:-}"
API_ISSUER="${APPLE_API_ISSUER:-}"
WINDOWS_PFX="${TERMX_WINDOWS_PFX:-}"
WINDOWS_PFX_PASSWORD="${TERMX_WINDOWS_PFX_PASSWORD:-}"
SIGN_APP=""
REPO="${TERMX_GITHUB_REPO:-}"
TAG=""
DRY_RUN=0
DO_IMPORT=1

usage() {
  cat <<'EOF'
usage: setup_github_signing.sh [options]

options:
  --cer PATH                Developer ID Application .cer (DER or PEM) [required]
  --key PATH                matching private key (default: ~/certs/termx-developer-id.key)
  --api-key PATH            App Store Connect API key .p8 [required]
  --api-key-id ID           API Key ID [required]
  --api-issuer UUID         API Issuer ID [required]
  --p12-out PATH            where to write the .p12 (default: ~/certs/DeveloperID.p12)
  --windows-pfx PATH        optional Windows code-signing .pfx
  --windows-pfx-password P  optional .pfx password
  --sign-app PATH           also sign (and notarize) this .app in place
  --repo OWNER/NAME         GitHub repository (default: detected)
  --tag vX.Y.Z              trigger the Desktop release workflow for this tag
  --dry-run                 validate and print the plan only
  --no-import               do not import into the login keychain
  -h, --help                show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --cer) CER="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --api-key-id) API_KEY_ID="$2"; shift 2 ;;
    --api-issuer) API_ISSUER="$2"; shift 2 ;;
    --p12-out) P12_OUT="$2"; shift 2 ;;
    --windows-pfx) WINDOWS_PFX="$2"; shift 2 ;;
    --windows-pfx-password) WINDOWS_PFX_PASSWORD="$2"; shift 2 ;;
    --sign-app) SIGN_APP="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-import) DO_IMPORT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option $1" >&2; usage >&2; exit 1 ;;
  esac
done

for var in CER KEY API_KEY; do
  eval "value=\${$var}"
  case "$value" in
    "~/"*) eval "$var=\"\$HOME/\${$var#\\~/}\"" ;;
  esac
done
case "$P12_OUT" in "~/"*) P12_OUT="$HOME/${P12_OUT#~/}" ;; esac
case "$WINDOWS_PFX" in "~/"*) WINDOWS_PFX="$HOME/${WINDOWS_PFX#~/}" ;; esac

if [ "$(uname -s)" != "Darwin" ]; then
  echo "error: run this on your Mac" >&2
  exit 1
fi

for required in CER API_KEY; do
  eval "value=\${$required}"
  if [ -z "$value" ]; then
    echo "error: missing --$(echo "$required" | tr '_' '-' | tr 'A-Z' 'a-z')" >&2
    usage >&2
    exit 1
  fi
done
for pair in "CER:$CER" "KEY:$KEY" "API_KEY:$API_KEY"; do
  name="${pair%%:*}"
  path="${pair#*:}"
  if [ ! -f "$path" ]; then
    echo "error: $name not found: $path" >&2
    exit 1
  fi
done
if [ -n "$WINDOWS_PFX" ] && [ ! -f "$WINDOWS_PFX" ]; then
  echo "error: --windows-pfx not found: $WINDOWS_PFX" >&2
  exit 1
fi
if [ -z "$API_KEY_ID" ] || [ -z "$API_ISSUER" ]; then
  echo "error: --api-key-id and --api-issuer are required" >&2
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/termx-setup.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

if openssl x509 -in "$CER" -noout -subject >/dev/null 2>&1; then
  PEM="$CER"
else
  PEM="$TMP/cert.pem"
  openssl x509 -inform DER -in "$CER" -out "$PEM" 2>/dev/null ||
    { echo "error: could not read $CER as a certificate" >&2; exit 1; }
fi

SUBJECT="$(openssl x509 -in "$PEM" -noout -subject)"
IDENTITY="$(printf '%s\n' "$SUBJECT" | sed -n 's/.*CN *= *\([^,/]*\).*/\1/p')"
case "$IDENTITY" in
  "Developer ID Application"*) : ;;
  *)
    echo "error: this is not a Developer ID Application certificate." >&2
    echo "       got: $IDENTITY" >&2
    echo "       In developer.apple.com choose Certificates -> + -> Software ->" >&2
    echo "       Developer ID Application (not Apple Distribution)." >&2
    exit 1
    ;;
esac
echo "==> certificate: $IDENTITY"

CERT_MOD="$(openssl x509 -in "$PEM" -noout -modulus 2>/dev/null | openssl md5)"
KEY_MOD="$(openssl rsa -in "$KEY" -noout -modulus 2>/dev/null | openssl md5)"
if [ -z "$CERT_MOD" ] || [ "$CERT_MOD" != "$KEY_MOD" ]; then
  echo "error: the private key does not match the certificate" >&2
  exit 1
fi
echo "==> private key matches"

if ! grep -q "BEGIN PRIVATE KEY" "$API_KEY"; then
  echo "error: $API_KEY does not look like an App Store Connect .p8 key" >&2
  exit 1
fi
echo "==> api key: $API_KEY_ID / $API_ISSUER"

if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "dry run plan:"
  echo "  1. write $P12_OUT (legacy encrypted)"
  [ "$DO_IMPORT" = "1" ] && echo "  2. import into the login keychain"
  echo "  3. write $ENV_FILE"
  [ -n "$SIGN_APP" ] && echo "  4. sign + notarize $SIGN_APP"
  echo "  5. push secrets to GitHub"
  [ -n "$TAG" ] && echo "  6. trigger the Desktop release workflow for $TAG"
  echo
  echo "dry run complete; nothing was changed."
  exit 0
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  :
else
  echo "warning: gh is not authenticated; secrets will not be uploaded" >&2
fi

P12_PASSWORD="$(openssl rand -hex 16)"
openssl pkcs12 -export -legacy -out "$P12_OUT" -inkey "$KEY" -in "$PEM" \
  -passout "pass:$P12_PASSWORD" -name "$IDENTITY" >/dev/null
chmod 600 "$P12_OUT"
echo "==> wrote $P12_OUT"

if [ "$DO_IMPORT" = "1" ]; then
  if security import "$P12_OUT" -k "$HOME/Library/Keychains/login.keychain-db" \
    -P "$P12_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security >/dev/null 2>&1; then
    echo "==> imported into the login keychain"
  else
    echo "warning: keychain import failed; import it manually with Keychain Access if needed" >&2
  fi
  if security find-identity -v -p codesigning 2>/dev/null | grep -F "Developer ID Application" >/dev/null 2>&1; then
    echo "==> codesigning identity is available"
  else
    echo "warning: identity not listed yet; codesign may still find it" >&2
  fi
fi

{
  echo "# Generated by desktop/scripts/setup_github_signing.sh"
  echo "# Local secrets for signing/notarization. gitignored — never commit."
  echo "APPLE_CER_PATH=$CER"
  echo "APPLE_PRIVATE_KEY_PATH=$KEY"
  echo "APPLE_P12_PATH=$P12_OUT"
  echo "APPLE_CERTIFICATE_PASSWORD=$P12_PASSWORD"
  echo "APPLE_SIGNING_IDENTITY=$IDENTITY"
  echo "APPLE_API_KEY_PATH=$API_KEY"
  echo "APPLE_API_KEY_ID=$API_KEY_ID"
  echo "APPLE_API_ISSUER=$API_ISSUER"
  if [ -n "$WINDOWS_PFX" ]; then
    echo "TERMX_WINDOWS_PFX=$WINDOWS_PFX"
    echo "TERMX_WINDOWS_PFX_PASSWORD=${WINDOWS_PFX_PASSWORD:-}"
  fi
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "==> wrote $ENV_FILE"

if [ -n "$SIGN_APP" ]; then
  echo "==> signing $SIGN_APP"
  sh "$SCRIPT_DIR/sign_macos_release.sh" --in-place --input "$SIGN_APP"
fi

if gh auth status >/dev/null 2>&1; then
  echo "==> uploading GitHub secrets"
  set -- --repo "$REPO" \
    --p12 "$P12_OUT" --p12-password "$P12_PASSWORD" \
    --identity "$IDENTITY" \
    --api-key "$API_KEY" --api-key-id "$API_KEY_ID" --api-issuer "$API_ISSUER"
  if [ -n "$WINDOWS_PFX" ]; then
    set -- "$@" --windows-pfx "$WINDOWS_PFX"
  fi
  if [ -n "$WINDOWS_PFX_PASSWORD" ]; then
    set -- "$@" --windows-pfx-password "$WINDOWS_PFX_PASSWORD"
  fi
  sh "$SCRIPT_DIR/push_signing_secrets.sh" "$@"
else
  echo "note: run desktop/scripts/push_signing_secrets.sh later to upload secrets"
fi

if [ -n "$TAG" ]; then
  echo "==> triggering Desktop release for $TAG"
  gh workflow run desktop.yml --repo "${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}" \
    -f release_tag="$TAG" -f draft=false -f include_intel=true
fi

echo
echo "done."
