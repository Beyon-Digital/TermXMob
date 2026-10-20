# Native companion surfaces

The Rust controller (`src/companions.rs`) fetches authenticated snapshots and
accepted character packs from the verified local daemon. It owns animation
clocks, state priority and bounded local desktop movement. These adapters render
RGBA sprites directly; no webview, browser canvas, desktop capture, window title
or agent credential enters the bridge.

All bridge functions and native handles are main-thread-only. Rust borrows frame
buffers for one synchronous call; adapters copy or retain their own pixel data.
The native queue reports intentional click, drag and menu actions by slot. Only
a deliberate click activates the main TermX window. Transparent sprite pixels
allow input to pass to the application underneath.

- macOS uses nonactivating AppKit panels and CoreGraphics image drawing. Window
  metadata comes from the public CoreGraphics window list. Sleep, session and
  screen-lock notifications hide panels; accessibility reduced motion applies.
- Windows uses `WS_EX_LAYERED | WS_EX_NOACTIVATE` windows with premultiplied BGRA
  DIBs and `UpdateLayeredWindow`. Monitor work areas and DPI constrain movement.
  Secure desktop checks hide pets during lock; power suspension hides windows.
- Linux uses GTK3/Cairo and a transparent RGBA visual. X11 exposes stacking and
  per-sprite input regions. GTK animation preferences and session lock/sleep
  signals are respected. Wayland and desktops without a compositor report that
  the in-app habitat is required. Build dependencies are GTK3 and X11 headers.

Pure movement tests run without a desktop:

```sh
rustc --test desktop/src-tauri/src/companion_motion.rs -o /tmp/termx-companion-motion-tests
/tmp/termx-companion-motion-tests
```

`cargo check --locked` and `cargo test` in `desktop/src-tauri` are diagnostic
checks. Delivery binaries must be built and published by GitHub CI. Installed
platform qualification must additionally exercise transparent-area input,
nonactivation, dragging, menus, hidden-main-window playback, lock/sleep,
compositor fallback and mixed-DPI monitor removal; source compilation alone does
not confirm those behaviors.
