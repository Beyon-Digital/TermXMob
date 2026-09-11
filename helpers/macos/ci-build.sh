#!/bin/sh
# Build arm64 and x86_64 helpers on GitHub Actions macos runners.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
BIN="$ROOT/bin"
mkdir -p "$BIN"

build_arch() {
  pkg="$1"
  name="$2"
  arch="$3"
  (
    cd "$ROOT/$pkg"
    swift build -c release --arch "$arch" --disable-sandbox
    out="$(swift build -c release --arch "$arch" --show-bin-path --disable-sandbox)/$name"
    if [ ! -x "$out" ]; then
      echo "missing $out" >&2
      exit 1
    fi
    dest="$BIN/${name}-${arch}"
    cp "$out" "$dest"
    chmod 755 "$dest"
    echo "built $dest"
    file "$dest"
  )
}

lipo_pair() {
  name="$1"
  arm="$BIN/${name}-arm64"
  intel="$BIN/${name}-x86_64"
  fat="$BIN/$name"
  if [ -x "$arm" ] && [ -x "$intel" ]; then
    lipo -create "$arm" "$intel" -output "$fat"
    chmod 755 "$fat"
    echo "universal $fat"
    lipo -info "$fat"
  elif [ -x "$arm" ]; then
    cp "$arm" "$fat"
  elif [ -x "$intel" ]; then
    cp "$intel" "$fat"
  else
    echo "no slices for $name" >&2
    exit 1
  fi
}

build_arch TermxCapture termx-capture arm64
build_arch TermxCapture termx-capture x86_64
build_arch TermxVirtualDisplay termx-virtual-display arm64
build_arch TermxVirtualDisplay termx-virtual-display x86_64
lipo_pair termx-capture
lipo_pair termx-virtual-display
ls -l "$BIN"
file "$BIN"/termx-*
