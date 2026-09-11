#!/bin/sh
# Build helper binaries on a Mac with Swift (GitHub Actions macos runner).
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
BIN="$ROOT/bin"
mkdir -p "$BIN"

build_pkg() {
  pkg="$1"
  name="$2"
  (
    cd "$ROOT/$pkg"
    swift build -c release --disable-sandbox
    out="$(swift build -c release --show-bin-path --disable-sandbox)/$name"
    if [ ! -x "$out" ]; then
      echo "missing $out" >&2
      exit 1
    fi
    cp "$out" "$BIN/$name"
    chmod 755 "$BIN/$name"
    echo "built $BIN/$name"
  )
}

build_pkg TermxCapture termx-capture
build_pkg TermxVirtualDisplay termx-virtual-display
ls -l "$BIN"
