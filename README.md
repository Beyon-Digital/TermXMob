# Termx

Run a terminal on your laptop. Use it from your phone or browser.

## Server

```bash
uv sync
uv run termx
```

Optional:

```bash
uv run termx --passcode hunter2
uv run termx --passcode hunter2 --tunnel
```

`--tunnel` needs [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

The process prints LAN URLs, an optional Cloudflare URL, and a QR code. Open the URL or scan the QR. Sessions keep running if you disconnect.

## Desktop app

Build a native desktop host that bundles the Python server, the web UI, and the macOS
helpers. End users install the artifact for their OS and need no Python or tooling.

```bash
pnpm --dir app install --frozen-lockfile
uv sync --group packaging
uv run --group packaging python desktop/scripts/build_sidecar.py
cd desktop/src-tauri && cargo tauri build
```

Installers (`.dmg`, MSI/NSIS, `.deb`/`.rpm`/`.AppImage`) are produced by
`.github/workflows/desktop.yml`. Push a `vX.Y.Z` tag that matches the app version (or
run the workflow manually) and GitHub builds, signs, and publishes the release; see
[`desktop/README.md`](desktop/README.md) for signing, permissions onboarding, lifecycle,
and log locations.

### Installing unsigned builds

Releases without code-signing secrets are still published; macOS builds are ad-hoc
signed so Apple Silicon accepts them. First run:

**macOS** (Gatekeeper will warn)

- Right-click `Termx.app` → **Open** → **Open** again, or
- `xattr -dr com.apple.quarantine /Applications/Termx.app`, or
- System Settings → Privacy & Security → scroll down → **Open Anyway**.
- Screen Recording and Accessibility are requested the first time you open the Desktop
  view. Ad-hoc builds get a new identity on every update, so macOS may ask again; a
  Developer ID-signed release keeps the grant.

**Windows** (SmartScreen will warn)

- **More info** → **Run anyway**, or unblock before running:
  `Unblock-File .\Termx_*_x64-setup.exe`. MSI installs the same way.

**Linux**

- `chmod +x Termx*.AppImage && ./Termx*.AppImage`
  (if FUSE is unavailable: `./Termx*.AppImage --appimage-extract && ./squashfs-root/AppRun`)
- Debian/Ubuntu: `sudo apt install ./Termx_*.deb`
- Fedora/RHEL: `sudo dnf install --nogpgcheck ./Termx-*.rpm` or `sudo rpm -i Termx-*.rpm`

Each release attaches `SHA256SUMS-<platform>.txt`; verify with
`shasum -a 256 -c SHA256SUMS-macos-arm64.txt` (or `sha256sum -c`, GNU coreutils).

## Phone app

```bash
cd app
pnpm install
pnpm start
```

Expo Go on iOS/Android, or press `w` for web. Enter `host:port` from the server, or scan the QR.

## Serve the Expo web UI from Python

```bash
cd app && pnpm export:web
uv run termx
```

Python serves `app/dist` when that folder exists. Otherwise it serves the built-in terminal page.

## Host notes

- Config, tokens, and audit logs live in `~/.config/termx` (or `$TERMX_CONFIG_DIR`).
- `POST /api/pair` with the passcode returns a Bearer token; passcode still works for QR.
- CI: `.github/workflows/ci.yml` runs pytest + app `tsc` on Ubuntu. `.github/workflows/macos-helpers.yml` builds Swift helpers on `macos-15` for **arm64 and x86_64**, lipos a universal binary, and commits all of them to `helpers/macos/bin/` on `main`. Termx selects the slice that matches this machine. Sign locally with Command Line Tools (no Xcode.app):
  `cp helpers/macos/signing/signing.env.example helpers/macos/signing/signing.env && helpers/macos/sign.sh`
  Put a `.p12` path in `signing.env` to import into the persisted `helpers/macos/signing/termx.keychain-db` (gitignored).
- Desktop capture prefers that signed `termx-capture`, then `ffmpeg`, then `screencapture`/`grim`/`maim`. Grant Screen Recording on macOS.
- Virtual extra displays: Hyprland `hyprctl output create headless`, Sway `create_output`, X11 `xrandr --setmonitor`, or `termx-virtual-display` on PATH. GNOME/KDE only if `gdctl`/`kscreen-doctor` help lists virtual/create.
- Tunnels: `cloudflared`, `ngrok`, or `tailscale` on `PATH`. Named Cloudflare tunnels need a token on the profile.
- Stop (`Ctrl+C` / SIGTERM) drains tunnels, desktop pumps, virtual displays, then PTY process groups.
- `TERMX_CORS_ORIGINS` optional comma-separated list (default `*`).

## Remote screen and displays by platform

The desktop workspace lists real displays and streams the selected one; the display dock
switches physical or virtual outputs.

| Platform | Capture | Input | Virtual displays |
| --- | --- | --- | --- |
| macOS 13+ | signed `termx-capture` (ScreenCaptureKit, `--display <id>`); `ffmpeg`/`screencapture` fallbacks | CoreGraphics events (Accessibility) | signed `termx-virtual-display` helper (CGVirtualDisplay); BetterDisplay/deskpad |
| Windows 10+ | GDI `BitBlt` with Pillow (bundled); `ffmpeg gdigrab` fallback | `SendInput` + native clipboard | requires an IddCx virtual display driver (e.g. open-source Virtual Display Driver); driver-created outputs are detected, captured, and controlled |
| Linux X11 | `ffmpeg x11grab`, ImageMagick `import`, `maim`, `scrot`, `xwd` | `xdotool` | `xrandr --setmonitor` |
| Linux Wayland | `grim`, `ffmpeg` PipeWire/x11grab | `xdotool` (XWayland) / `ydotool` | Hyprland `hyprctl`, Sway `create_output`, GNOME `gdctl`, KDE `kscreen-doctor` |

Windows adds `pillow` automatically; Linux needs the usual desktop tools on `PATH`.
Unit and integration coverage lives in `tests/test_capture*.py`, `tests/test_displays.py`,
and the CI `linux-desktop` job (Xvfb + Xorg dummy output).
