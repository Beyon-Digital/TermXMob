# Termx desktop packaging analysis

Repository state analyzed at commit `0692123` (working tree has unrelated Expo UI edits).
This document is the required pre-change inventory. It is based on direct inspection of
`src/termx/**`, `app/**`, `helpers/**`, `tests/**`, and `.github/workflows/**` — not on a
generic FastAPI assumption.

## 1. Current runtime architecture

One long-lived Python process, no separate services:

- `src/termx/cli.py:65` `_serve()` builds `AppState` (`src/termx/app.py:33`) and runs a
  single `uvicorn.Server` with `FastAPI` + WebSockets. Default bind is `0.0.0.0:8787`
  (`cli.py:28-29`), `log_level="warning"`.
- `AppState` owns: `TokenStore`, `Auth`, `ConfigStore`, `SessionManager`, `TunnelManager`,
  `DesktopManager`, `RtcManager`, and the bind port.
- HTTP surface (`src/termx/app.py`): `/api/health`, `/api/pair`, `/api/audit`,
  `/api/machine`, `/api/preferences`, `/api/commands*`, `/api/fs`, `/api/directories*`,
  `/api/tunnels*`, `/api/displays*`, `/api/desktop/rtc/{offer,ice}`, `/api/sessions*`.
- WebSockets: `/api/sessions/{id}/pty` (binary PTY bytes + JSON control), and
  `/api/desktop/session` (JPEG frames + JSON control/clipboard).
- Static serving: Expo web export (`app/dist`) when a `--web-dir` exists, otherwise the
  bundled `src/termx/static/index.html`; `/_/embed.html` and `/_/vendor/*` are always
  served. SPA fallback rewrites unknown non-`api/`, non-`_/` paths to `index.html`
  (`app.py:648-680`).
- Shutdown: `SIGTERM`/`SIGINT` set `server.should_exit`; `shutdown_state()`
  (`src/termx/lifecycle.py:7`) drains tunnels → desktop pumps → capture helper → WebRTC →
  virtual displays → PTY process groups. The same drain runs in the FastAPI lifespan.
- State is process-local. There is no database. Everything durable is JSON under the
  config dir: `config.json`, `tokens.json`, `audit.jsonl` (`src/termx/config.py:20`).

## 2. Application entrypoints

- Console script `termx = termx:main` (`pyproject.toml:18`) → `termx.cli.main`.
- `python -m termx` via `src/termx/__main__.py`.
- CLI flags: `--host` (default `0.0.0.0`), `--port` (default `8787`), `--passcode`,
  `--tunnel` (Cloudflare quick tunnel at boot), `--web-dir`.
- Startup prints a text banner with LAN URL(s), optional tunnel URL, passcode, and an
  ASCII QR code (`cli.py:44-62`, `src/termx/net.py`).
- No Windows service/daemon, no scheduled tasks, no launch agents.

## 3. Frontend/backend relationship

- `app/` is an Expo Router (React Native + react-native-web) client. `app.json` sets
  `web.output: "single"` so `pnpm export:web` emits a client-routed SPA at `app/dist`
  with root-absolute asset paths.
- Web UI auto-connects same-origin (`app/src/app/index.tsx:27-36`): it reads
  `window.location`, takes `?k=` as the passcode, calls `/api/health`, then routes to
  `/workspace`. A desktop shell can therefore load `http://127.0.0.1:<port>/?k=<pass>`
  with zero frontend changes.
- Native clients (phone) resolve a `Connection {protocol,host,port,passcode}`
  (`app/src/lib/types.ts`), store it in SecureStore on native and localStorage on web,
  and talk REST + WebSockets to `${httpBase}`/`${wsBase}` with
  `X-Termx-Passcode`/`?k=` (`app/src/lib/api.ts`).
- Native terminal rendering uses an in-app WebView pointed at `/_/embed.html`
  (`app/src/components/terminal-view.native.tsx`); web uses xterm.js directly.
- `expo-camera` QR scan is native-only and already hidden on web
  (`app/src/app/index.tsx:249`). There is no `EXPO_PUBLIC_*` runtime configuration.

## 4. Existing native integrations

- **macOS capture helper** (`helpers/macos/TermxCapture`): Swift, ScreenCaptureKit,
  streams length-prefixed JPEG frames on stdout. Per-arch slices + universal binaries in
  `helpers/macos/bin/`. Resolution order in `src/termx/desktop/paths.py`: env override →
  `PATH` (`termx-capture-<arch>`, `termx-capture`) → repo-relative `helpers/macos/bin/`
  → Swift `.build/release/`; `lipo -info` filters wrong-arch slices.
- **Capture fallbacks** (`src/termx/desktop/capture.py`): helper → `ffmpeg`
  (avfoundation / pipewire / x11grab) → `screencapture` → `grim` → `maim` → `import` →
  `xwd`+`convert`.
- **Input injection** (`src/termx/desktop/input.py`): macOS `CGEventPost` through ctypes
  or `cliclick`/`osascript`; Linux `xdotool`/`ydotool`. Clipboard via
  `pbpaste`/`pbcopy`, `wl-clipboard`, `xclip`.
- **Virtual displays** (`src/termx/desktop/virtual.py`): macOS signed helper, Hyprland,
  Sway, XRandR, gnome `gdctl`, `kscreen-doctor` adapters with capability probes and
  lease expiry.
- **Tunnels** (`src/termx/providers/*`): `cloudflared` quick/named, `ngrok`,
  `tailscale` spawned as `asyncio` subprocesses, normalized state machine, redacted log
  ring.
- **WebRTC** (`src/termx/desktop/webrtc.py`): optional `aiortc`/`av`/`numpy`; absent
  from the dependency list today, so `/api/desktop/rtc/offer` returns 503 and clients
  use the JPEG WebSocket stream.

## 5. Existing permission-dependent features

- macOS **Screen Recording**: required by `termx-capture` (ScreenCaptureKit), by
  `screencapture`, and by `ffmpeg` avfoundation. TCC attributes the grant to the
  *responsible* process — today that is the user's terminal app; after installation it
  becomes the Termx app bundle.
- macOS **Accessibility**: `CGEventPost` without a `CGEventSource` from the Python
  process, and `osascript` → `System Events` keystrokes. Also attributed to the
  responsible app.
- macOS **Automation** (`osascript` keystroke) may raise a second TCC prompt.
- Linux: Wayland portal consent for PipeWire capture, compositor-specific virtual-output
  permission, `ydotool`/`uinput` group membership, X11 `xdotool`.
- Surface: `probe_desktop()` returns `permissions` as `"unknown"` on macOS today
  (`src/termx/desktop/capabilities.py:42-45`); there is no status/request API and no
  onboarding UI.

## 6. Existing tunnelling/network behavior

- Server binds `0.0.0.0` by default, i.e. LAN is a first-class product feature, not an
  accident. CORS is `*` unless `TERMX_CORS_ORIGINS` is set
  (`app.py:128-134`). WebSocket auth accepts `?k=` because browsers cannot set headers.
- Passcode is optional; when absent every request is authenticated by default
  (`src/termx/auth.py:17-24`). `/api/pair` issues SHA-256-hashed bearer tokens
  (`src/termx/tokens.py`).
- Tunnels are explicit user actions (UI) or the `--tunnel` boot flag, and target the
  local server port. Provider availability is discovered via `shutil.which`.
- `net.http_urls()` computes loopback + LAN IPv4 URLs; `qr_ascii()` renders the pairing
  QR in the terminal.

## 7. Existing background processes

- One OS PTY child per terminal session plus a daemon reader thread
  (`src/termx/sessions.py:133`); sessions survive client disconnects and are reaped on
  shutdown.
- A long-lived capture-helper subprocess reused across frames
  (`src/termx/desktop/capture.py:119-155`), terminated on capture failure or shutdown.
- One subprocess per active tunnel provider, plus log pumps
  (`src/termx/providers/*`).
- A daemon asyncio loop thread for WebRTC when `aiortc` is importable
  (`webrtc.py:84-91`).
- No child-process supervision beyond in-process managers; if the main process dies,
  children can leak (PTY children get SIGHUP because of the controlling TTY, but helper
  and tunnel children do not).

## 8. Packaging-sensitive dependencies

- `requires-python >= 3.14`; deps: FastAPI, uvicorn[standard] (uvloop, httptools,
  watchfiles, websockets, python-dotenv), qrcode.
- POSIX-only imports at module top level in `sessions.py`: `fcntl`, `termios`, `pty`,
  `select`, plus `preexec_fn`, `os.killpg`, `os.getpgid`. This makes `import termx`
  fail on Windows and blocks a Windows installer until refactored.
- `helpers_bin_dir()` is repo-relative (`Path(__file__).parents[3]`). Frozen bundles
  break this.
- `default_web_dir()` searches `./app/dist` and `../app/dist` relative to CWD. A GUI
  launch from `/` or `C:\Windows\System32` breaks this.
- `PACKAGE_STATIC` is package-relative and survives PyInstaller only if
  `termx/static` is collected as data.
- Optional `aiortc`/`av`/`numpy` must be conditionally collected; they are not
  installed by default.
- Swift helpers are Mach-O binaries that must keep valid signatures inside the app
  bundle; PyInstaller `binaries=` processing can invalidate signatures, so they should
  ride as data files and be re-signed/chmod'ed.
- `uvicorn[standard]` pulls compiled wheels (uvloop/httptools/watchfiles) that
  PyInstaller must collect.

## 9. Platform-specific code already present

- macOS: Swift helpers, `lipo` arch matching, ctypes CoreGraphics input, `pbpaste`,
  `screencapture`, `osascript`, virtual-display helper.
- Linux: X11/Wayland detection, `xdotool`/`ydotool`, `grim`/`maim`/`import`/`xwd`,
  Hyprland/Sway/XRandR/GNOME/KDE virtual outputs.
- Windows: only an explicit unsupported probe result
  (`capabilities.py:75-80`) and `ffmpeg` capture refusal (`capture.py:241`). There is no
  PTY/session backend, so Windows is currently unusable even from source.
- iOS/Android: mobile client only (`app/app.json`), not a host platform.

## 10. Assumptions that break once installed rather than run from source

1. **Repo-relative helper lookup** — the resulting `.app`/install has no `helpers/`
   directory above the package. Fix: bundled-resource lookup + `TERMX_*_BIN` env
   injected by the shell.
2. **CWD-relative web export** — GUI launches have arbitrary CWD. Fix: resolve
   `sys._MEIPASS/web` first, and let the shell pass `--web-dir` explicitly.
3. **GUI `PATH`** — Finder/Explorer launches get a minimal `PATH`, so `cloudflared`,
   `ngrok`, `tailscale`, `ffmpeg`, `grim`, `xdotool` disappear even when installed.
   Fix: augment `PATH` from `/etc/paths` + Homebrew/common Windows locations before any
   `shutil.which` call.
4. **Python availability** — end users have no `uv`/venv. Fix: PyInstaller onedir
   sidecar bundled as a Tauri resource.
5. **Port 8787 assumed free** — another app or a stale Termx may hold it. Fix: probe,
   adopt a live Termx when the stored passcode works, otherwise allocate the next free
   port and report it.
6. **Stale child processes** — if the host dies, capture helper/tunnels can leak. Fix:
   shell kills the sidecar process group/tree on exit and on crash; backend keeps the
   existing `shutdown_state` drain for graceful exits.
7. **TCC attribution** — screen recording/accessibility grants currently belong to the
   user's terminal. Installed, they must be requested by and granted to `Termx.app`.
   Fix: status/request APIs plus shell onboarding; nested helper binaries signed inside
   the bundle.
8. **Unsigned nested helpers under a notarized app** — Gatekeeper kills modified or
   unsigned Mach-O files. Fix: sign inner→outer, include entitlements, notarize the
   sidecar's executables.
9. **Config dir convention** — `~/.config/termx` on macOS is not the native location.
   Fix: shell sets `TERMX_CONFIG_DIR` to the per-OS app-data directory and migrates the
   legacy files once.
10. **No single-instance/lifecycle owner** — double-launch currently means two servers
    and two tray-less processes. Fix: single-instance plugin + pid/lock file + health
    adoption.
11. **Console-only UX** — banners/QR only exist in the terminal. Fix: tray, native
    menu, connection-info window with the existing `qrcode` renderer, native
    notifications.
12. **Windows import failure** — `termx` cannot even import on Windows because of
    `termios`/`fcntl`. Fix: platform terminal backend abstraction with a ConPTY
    implementation.
