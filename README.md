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
- CI: `.github/workflows/ci.yml` runs pytest + app `tsc` on Ubuntu. `.github/workflows/macos-helpers.yml` builds Swift helpers on `macos-15` and commits unsigned binaries to `helpers/macos/bin/` on `main` (also downloadable as the `termx-macos-helpers` artifact). Sign locally with Command Line Tools (no Xcode.app):
  `cp helpers/macos/signing/signing.env.example helpers/macos/signing/signing.env && helpers/macos/sign.sh`
  Put a `.p12` path in `signing.env` to import into the persisted `helpers/macos/signing/termx.keychain-db` (gitignored).
- Desktop capture prefers that signed `termx-capture`, then `ffmpeg`, then `screencapture`/`grim`/`maim`. Grant Screen Recording on macOS.
- Virtual extra displays: Hyprland `hyprctl output create headless`, Sway `create_output`, X11 `xrandr --setmonitor`, or `termx-virtual-display` on PATH. GNOME/KDE only if `gdctl`/`kscreen-doctor` help lists virtual/create.
- Tunnels: `cloudflared`, `ngrok`, or `tailscale` on `PATH`. Named Cloudflare tunnels need a token on the profile.
- Stop (`Ctrl+C` / SIGTERM) drains tunnels, desktop pumps, virtual displays, then PTY process groups.
- `TERMX_CORS_ORIGINS` optional comma-separated list (default `*`).
