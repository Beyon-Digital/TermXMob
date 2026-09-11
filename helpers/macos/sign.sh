#!/bin/sh
# Sign CI-built helpers with /usr/bin/codesign (Command Line Tools).
# Does not invoke Xcode.app or xcodebuild.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
SIGN_DIR="$ROOT/signing"
BIN_DIR="$ROOT/bin"
ENV_FILE="$SIGN_DIR/signing.env"

CODESIGN_IDENTITY="-"
P12_PATH=""
P12_PASSWORD=""
KEYCHAIN_PASSWORD="termx-signing"
KEYCHAIN_PATH="$SIGN_DIR/termx.keychain-db"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

if [ -n "${KEYCHAIN_PATH:-}" ]; then
  :
else
  KEYCHAIN_PATH="$SIGN_DIR/termx.keychain-db"
fi

CODESIGN="$(command -v codesign || true)"
if [ -z "$CODESIGN" ] && [ -x /usr/bin/codesign ]; then
  CODESIGN=/usr/bin/codesign
fi
if [ -z "$CODESIGN" ]; then
  echo "codesign not found. Install Apple Command Line Tools: xcode-select --install" >&2
  exit 1
fi

ensure_keychain() {
  if [ -z "${P12_PATH:-}" ]; then
    return 0
  fi
  if [ ! -f "$P12_PATH" ]; then
    echo "P12_PATH not found: $P12_PATH" >&2
    exit 1
  fi
  mkdir -p "$SIGN_DIR"
  if [ ! -f "$KEYCHAIN_PATH" ]; then
    security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
  fi
  security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH" >/dev/null
  security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
  security list-keychains -d user -s "$KEYCHAIN_PATH"
  security import "$P12_PATH" -k "$KEYCHAIN_PATH" -P "${P12_PASSWORD:-}" -T /usr/bin/codesign -T "$CODESIGN" >/dev/null || true
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH" >/dev/null
  if [ "$CODESIGN_IDENTITY" = "-" ] || [ -z "$CODESIGN_IDENTITY" ]; then
    CODESIGN_IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" | awk -F'"' '/Developer ID Application|Apple Development|Mac Developer/{print $2; exit}')"
  fi
  if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    echo "No codesigning identity in $KEYCHAIN_PATH" >&2
    exit 1
  fi
  echo "using identity: $CODESIGN_IDENTITY"
}

sign_one() {
  name="$1"
  base="$name"
  case "$name" in
    *-arm64) base="${name%-arm64}" ;;
    *-x86_64) base="${name%-x86_64}" ;;
  esac
  entitlements="$SIGN_DIR/${base}.entitlements"
  target="$BIN_DIR/$name"
  if [ ! -f "$target" ]; then
    echo "skip $name (missing $target — wait for GitHub Actions build)" >&2
    return 0
  fi
  extra=""
  if [ -f "$entitlements" ]; then
    extra="--entitlements $entitlements"
  fi
  # timestamp omitted: local CLT signing; notarization is not required for LAN helpers
  # shellcheck disable=SC2086
  "$CODESIGN" --force --sign "$CODESIGN_IDENTITY" --options runtime $extra "$target"
  "$CODESIGN" --verify --verbose=2 "$target"
  echo "signed $target ($CODESIGN_IDENTITY)"
}

ensure_keychain
mkdir -p "$BIN_DIR"
for name in \
  termx-capture \
  termx-capture-arm64 \
  termx-capture-x86_64 \
  termx-virtual-display \
  termx-virtual-display-arm64 \
  termx-virtual-display-x86_64
do
  sign_one "$name"
done
