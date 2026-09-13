#!/bin/sh
# Push the Apple (and optional Windows) code-signing secrets to GitHub so the
# Desktop release workflow can sign and notarize builds.
#
# Nothing here is your Apple ID or password: you use a Developer ID Application
# certificate plus an App Store Connect API key.
#
# Typical run (values come from desktop/signing.env or flags; secrets are read
# from files or prompted, never echoed):
#
#   desktop/scripts/push_signing_secrets.sh \
#     --p12 ~/certs/DeveloperID.p12 \
#     --api-key ~/Downloads/AuthKey_ABC123XYZ.p8 \
#     --api-key-id ABC123XYZ \
#     --api-issuer 00000000-0000-0000-0000-000000000000
#
# Preview without touching GitHub:
#
#   desktop/scripts/push_signing_secrets.sh --dry-run ...
#
# See desktop/SIGNING.md for what to get from Apple.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

# shellcheck disable=SC1090
if [ -f "$REPO_ROOT/desktop/signing.env" ]; then
  if sh -n "$REPO_ROOT/desktop/signing.env" 2>/dev/null; then
    set -a
    . "$REPO_ROOT/desktop/signing.env"
    set +a
  else
    echo "warning: ignoring invalid desktop/signing.env" >&2
  fi
fi

REPO="${TERMX_GITHUB_REPO:-}"
P12="${APPLE_P12_PATH:-}"
P12_PASSWORD="${APPLE_CERTIFICATE_PASSWORD:-}"
IDENTITY="${APPLE_SIGNING_IDENTITY:-}"
API_KEY="${APPLE_API_KEY_PATH:-}"
API_KEY_ID="${APPLE_API_KEY_ID:-}"
API_ISSUER="${APPLE_API_ISSUER:-}"
WINDOWS_PFX="${TERMX_WINDOWS_PFX:-}"
WINDOWS_PFX_PASSWORD="${TERMX_WINDOWS_PFX_PASSWORD:-}"
KEYCHAIN_PASSWORD="${KEYCHAIN_PASSWORD:-}"
DRY_RUN=0

usage() {
  cat <<'EOF'
usage: push_signing_secrets.sh [options]

options:
  --repo OWNER/NAME           repository (default: detected from the git remote)
  --p12 PATH                  Developer ID Application .p12 (cert + private key)
  --p12-password PASS         .p12 password (or APPLE_CERTIFICATE_PASSWORD / prompt)
  --identity NAME             codesign identity (default: extracted from the .p12)
  --api-key PATH              App Store Connect API private key (.p8)
  --api-key-id ID             10-character API Key ID
  --api-issuer UUID           API Issuer ID
  --windows-pfx PATH          optional Windows code-signing .pfx
  --windows-pfx-password PASS optional .pfx password
  --keychain-password PASS    optional CI keychain password (generated if omitted)
  --dry-run                   validate and print the plan without setting secrets
  -h, --help                  show this help

environment / desktop/signing.env:
  APPLE_P12_PATH, APPLE_CERTIFICATE_PASSWORD, APPLE_SIGNING_IDENTITY
  APPLE_API_KEY_PATH, APPLE_API_KEY_ID, APPLE_API_ISSUER
  TERMX_WINDOWS_PFX, TERMX_WINDOWS_PFX_PASSWORD, KEYCHAIN_PASSWORD
  TERMX_GITHUB_REPO
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --p12) P12="$2"; shift 2 ;;
    --p12-password) P12_PASSWORD="$2"; shift 2 ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --api-key-id) API_KEY_ID="$2"; shift 2 ;;
    --api-issuer) API_ISSUER="$2"; shift 2 ;;
    --windows-pfx) WINDOWS_PFX="$2"; shift 2 ;;
    --windows-pfx-password) WINDOWS_PFX_PASSWORD="$2"; shift 2 ;;
    --keychain-password) KEYCHAIN_PASSWORD="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option $1" >&2; usage >&2; exit 1 ;;
  esac
done

case "$P12" in
  "~/"*) P12="$HOME/${P12#\~/}" ;;
esac
case "$API_KEY" in
  "~/"*) API_KEY="$HOME/${API_KEY#\~/}" ;;
esac
case "$WINDOWS_PFX" in
  "~/"*) WINDOWS_PFX="$HOME/${WINDOWS_PFX#\~/}" ;;
esac

prompt_secret() {
  # $1 = variable name, $2 = prompt
  printf '%s' "$2" >&2
  if [ -t 0 ]; then
    stty -echo 2>/dev/null || true
  fi
  IFS= read -r value
  if [ -t 0 ]; then
    stty echo 2>/dev/null || true
    printf '\n' >&2
  fi
  eval "$1=\$value"
}

if ! command -v gh >/dev/null 2>&1; then
  echo "error: the GitHub CLI (gh) is required: https://cli.github.com" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "error: run 'gh auth login' first (needs repo + workflow scope)" >&2
  exit 1
fi

if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
fi
if [ -z "$REPO" ]; then
  echo "error: could not detect the repository; pass --repo OWNER/NAME" >&2
  exit 1
fi

if [ -z "$P12" ]; then
  echo "error: Developer ID .p12 is required (--p12 or APPLE_P12_PATH)" >&2
  echo "       see desktop/SIGNING.md" >&2
  exit 1
fi
if [ ! -f "$P12" ]; then
  echo "error: .p12 not found: $P12" >&2
  exit 1
fi
if [ -z "$P12_PASSWORD" ]; then
  if [ -t 0 ]; then
    prompt_secret P12_PASSWORD "Password for $(basename "$P12"): "
  else
    echo "error: .p12 password required (--p12-password or APPLE_CERTIFICATE_PASSWORD)" >&2
    exit 1
  fi
fi
if ! openssl pkcs12 -in "$P12" -passin "pass:$P12_PASSWORD" -noout >/dev/null 2>&1; then
  # LibreSSL needs -legacy for some AES-256 p12 files.
  if ! openssl pkcs12 -legacy -in "$P12" -passin "pass:$P12_PASSWORD" -noout >/dev/null 2>&1; then
    echo "error: could not read the .p12; check the password and file" >&2
    exit 1
  fi
fi

extract_identity() {
  # Apple subjects look like "UID=TEAMID, CN=Developer ID Application: ... (TEAMID), OU=..."
  # as well as the older "/CN=.../O=..." form, so match the CN component directly.
  openssl pkcs12 "$@" -nokeys -clcerts 2>/dev/null |
    openssl x509 -noout -subject 2>/dev/null |
    sed -n 's/.*CN *= *\([^,/]*\).*/\1/p'
}

if [ -z "$IDENTITY" ]; then
  IDENTITY="$(extract_identity -in "$P12" -passin "pass:$P12_PASSWORD" || true)"
  if [ -z "$IDENTITY" ]; then
    IDENTITY="$(extract_identity -legacy -in "$P12" -passin "pass:$P12_PASSWORD" || true)"
  fi
fi
case "$IDENTITY" in
  "Developer ID Application"*) : ;;
  "") echo "error: could not determine the identity; pass --identity" >&2; exit 1 ;;
  *) echo "warning: identity does not start with 'Developer ID Application': $IDENTITY" >&2 ;;
esac

if [ -z "$API_KEY" ] || [ -z "$API_KEY_ID" ] || [ -z "$API_ISSUER" ]; then
  echo "error: --api-key, --api-key-id, and --api-issuer are all required" >&2
  echo "       create the key at appstoreconnect.apple.com -> Users and Access -> Integrations" >&2
  exit 1
fi
if [ ! -f "$API_KEY" ]; then
  echo "error: API key .p8 not found: $API_KEY" >&2
  exit 1
fi
if ! grep -q "BEGIN PRIVATE KEY" "$API_KEY"; then
  echo "error: $API_KEY does not look like a .p8 private key" >&2
  exit 1
fi
case "$API_KEY_ID" in
  ??????????) : ;;
  *) echo "warning: API key id is usually 10 characters: $API_KEY_ID" >&2 ;;
esac

if [ -z "$KEYCHAIN_PASSWORD" ]; then
  KEYCHAIN_PASSWORD="$(openssl rand -hex 16)"
fi

if [ -n "$WINDOWS_PFX" ] && [ ! -f "$WINDOWS_PFX" ]; then
  echo "error: Windows .pfx not found: $WINDOWS_PFX" >&2
  exit 1
fi

set_secret() {
  name="$1"
  value="$2"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  would set $name"
    return 0
  fi
  printf '%s' "$value" | gh secret set "$name" --repo "$REPO"
  echo "  set $name"
}

set_secret_file() {
  name="$1"
  file="$2"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  would set $name (from $(basename "$file"))"
    return 0
  fi
  gh secret set "$name" --repo "$REPO" < "$file"
  echo "  set $name (from $(basename "$file"))"
}

echo "==> repository: $REPO"
echo "==> identity:   $IDENTITY"
echo "==> api key:    $API_KEY_ID / $API_ISSUER"
if [ -n "$WINDOWS_PFX" ]; then
  echo "==> windows:    $WINDOWS_PFX"
fi
if [ "$DRY_RUN" = "1" ]; then echo "==> setting secrets (dry run)"; else echo "==> setting secrets"; fi
echo

set_secret APPLE_CERTIFICATE "$(base64 < "$P12" | tr -d '\n')"
set_secret APPLE_CERTIFICATE_PASSWORD "$P12_PASSWORD"
set_secret APPLE_SIGNING_IDENTITY "$IDENTITY"
set_secret APPLE_API_KEY_ID "$API_KEY_ID"
set_secret APPLE_API_ISSUER "$API_ISSUER"
set_secret_file APPLE_API_KEY_P8 "$API_KEY"
set_secret KEYCHAIN_PASSWORD "$KEYCHAIN_PASSWORD"

if [ -n "$WINDOWS_PFX" ]; then
  set_secret WINDOWS_CERTIFICATE "$(base64 < "$WINDOWS_PFX" | tr -d '\n')"
  if [ -z "$WINDOWS_PFX_PASSWORD" ]; then
    if [ -t 0 ]; then
      prompt_secret WINDOWS_PFX_PASSWORD "Password for $(basename "$WINDOWS_PFX"): "
    fi
  fi
  if [ -z "$WINDOWS_PFX_PASSWORD" ]; then
    echo "error: --windows-pfx-password required with --windows-pfx" >&2
    exit 1
  fi
  set_secret WINDOWS_CERTIFICATE_PASSWORD "$WINDOWS_PFX_PASSWORD"
fi

if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "dry run complete; nothing was uploaded."
  exit 0
fi

echo
echo "==> configured secrets:"
gh secret list --repo "$REPO" | awk 'NR==1 || /^APPLE_|^KEYCHAIN_|^WINDOWS_/'
echo
echo "Next: Actions -> Desktop release -> Run workflow (release_tag vX.Y.Z)."
