#!/bin/sh
# Sign Termx Linux artifacts with GPG — no arguments required.
#
#   desktop/scripts/sign_linux_release.sh
#
# Discovers the newest .deb, .rpm, and .AppImage under the build output, ./dist, the
# current directory, and ~/Downloads. Use --all to sign everything it finds.
#
# GPG key selection comes from the environment and desktop/signing.env:
#   TERMX_GPG_KEY=you@example.com
#
# Every artifact gets a detached ASCII-armored signature (<artifact>.asc). When
# dpkg-sig/rpm are installed, package metadata is signed in place too.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"

# shellcheck disable=SC1090
if [ -f "$REPO_ROOT/desktop/signing.env" ]; then
  set -a
  . "$REPO_ROOT/desktop/signing.env"
  set +a
fi

GPG_KEY="${TERMX_GPG_KEY:-}"
ALL=0
DRY_RUN=0
INPUTS=""

usage() {
  cat <<'EOF'
usage: sign_linux_release.sh [options] [artifact...]

With no artifact it signs the newest .deb/.rpm/.AppImage found in:
  desktop/src-tauri/target/release/bundle/{deb,rpm,appimage}, ./dist, ., ~/Downloads

options:
  --all             sign every discovered artifact
  --input PATH      search PATH (file or directory); repeatable
  --key KEYID       GPG key id/email (default: TERMX_GPG_KEY or the default key)
  --dry-run         print what would be signed and exit
  -h, --help        show this help

environment / desktop/signing.env:
  TERMX_GPG_KEY
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --all) ALL=1; shift ;;
    --input) INPUTS="$INPUTS$2
"; shift 2 ;;
    --key) GPG_KEY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "error: unknown option $1" >&2; usage >&2; exit 1 ;;
    *) INPUTS="$INPUTS$1
"; shift ;;
  esac
done

mtime() {
  stat -c '%Y' "$1" 2>/dev/null || stat -f '%m' "$1" 2>/dev/null || echo 0
}

newest() {
  [ -n "$1" ] || return 1
  printf '%s\n' "$1" | while IFS= read -r candidate; do
    [ -n "$candidate" ] && printf '%s\t%s\n' "$(mtime "$candidate")" "$candidate"
  done | sort -rn | head -n 1 | cut -f2-
}

collect_from() {
  root="$1"
  if [ -f "$root" ]; then
    case "$root" in
      *.deb|*.rpm|*.AppImage|*.appimage) printf '%s\n' "$root" ;;
    esac
    return 0
  fi
  [ -d "$root" ] || return 0
  find "$root" -maxdepth 2 -type f \( -name '*.deb' -o -name '*.rpm' -o -iname '*.appimage' \) 2>/dev/null || true
}

discover() {
  if [ -n "$INPUTS" ]; then
    roots="$INPUTS"
  elif [ -n "${TERMX_SIGN_INPUT:-}" ]; then
    roots="$TERMX_SIGN_INPUT"
  else
    roots="$REPO_ROOT/desktop/src-tauri/target/release/bundle/deb
$REPO_ROOT/desktop/src-tauri/target/release/bundle/rpm
$REPO_ROOT/desktop/src-tauri/target/release/bundle/appimage
$REPO_ROOT/dist
$PWD
$HOME/Downloads"
  fi
  printf '%s\n' "$roots" | while IFS= read -r root; do
    [ -n "$root" ] && collect_from "$root"
  done | awk 'NF && !seen[$0]++'
}

ARTIFACTS="$(discover)"
DEBS="$(printf '%s\n' "$ARTIFACTS" | grep '\.deb$' || true)"
RPMS="$(printf '%s\n' "$ARTIFACTS" | grep '\.rpm$' || true)"
APPIMAGES="$(printf '%s\n' "$ARTIFACTS" | grep -i '\.appimage$' || true)"

if [ "$ALL" = "1" ]; then
  SELECTED="$ARTIFACTS"
else
  SELECTED=""
  [ -n "$DEBS" ] && SELECTED="$SELECTED$(newest "$DEBS")
"
  [ -n "$RPMS" ] && SELECTED="$SELECTED$(newest "$RPMS")
"
  [ -n "$APPIMAGES" ] && SELECTED="$SELECTED$(newest "$APPIMAGES")
"
fi

if [ -z "$(printf '%s' "$SELECTED" | tr -d '[:space:]')" ]; then
  platform="$(uname -s)"
  if [ "$platform" = "Linux" ]; then
    echo "error: no .deb, .rpm, or .AppImage found." >&2
    echo "hint: download them from the GitHub release, pass a path, set TERMX_SIGN_INPUT=/path, or build with:" >&2
    echo "      cargo tauri build --bundles deb,rpm,appimage   (in desktop/src-tauri)" >&2
  else
    echo "error: no .deb, .rpm, or .AppImage found on this $platform machine." >&2
    echo "note: Linux packages are built by the Linux CI runner (or a Linux machine); macOS builds" >&2
    echo "      produce only .dmg/.app and Windows builds produce .msi/.exe." >&2
    echo "hint: download the Linux artifacts from the GitHub release, then run:" >&2
    echo "      desktop/scripts/sign_linux_release.sh --input ~/Downloads" >&2
  fi
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
  echo "==> dry run; gpg key: ${GPG_KEY:-<default>}"
  exit 0
fi

if ! command -v gpg >/dev/null 2>&1; then
  echo "error: gpg is required to sign Linux artifacts" >&2
  exit 1
fi

while IFS= read -r file; do
  [ -n "$file" ] || continue
  if [ ! -f "$file" ]; then
    echo "error: not found: $file" >&2
    exit 1
  fi
  echo "==> signing $file"
  if [ -n "$GPG_KEY" ]; then
    gpg --batch --yes --local-user "$GPG_KEY" --detach-sign --armor "$file"
  else
    gpg --batch --yes --detach-sign --armor "$file"
  fi

  case "$file" in
    *.deb)
      if command -v dpkg-sig >/dev/null 2>&1; then
        if [ -n "$GPG_KEY" ]; then
          dpkg-sig --sign builder -k "$GPG_KEY" "$file"
        else
          dpkg-sig --sign builder "$file"
        fi
      fi
      ;;
    *.rpm)
      if command -v rpm >/dev/null 2>&1 && [ -n "$GPG_KEY" ]; then
        rpm --addsign --define "_gpg_name $GPG_KEY" "$file"
      fi
      ;;
  esac
done < "$SELECT_FILE"
rm -f "$SELECT_FILE"

echo
echo "done. signatures written next to each artifact (.asc)."
echo "Unsigned installs are fine for most users:"
echo "  chmod +x Termx*.AppImage && ./Termx*.AppImage"
echo "  sudo apt install ./Termx*.deb   # or: sudo dnf install ./Termx*.rpm"
echo "Verify with: gpg --verify Termx.AppImage.asc Termx.AppImage"
