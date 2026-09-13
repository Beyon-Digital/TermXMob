# Termx desktop packaging plan

Status: `approved-by-directive` (the requesting message mandates this work; no further
approval gate is required).

Goal: ship `Termx.app` (macOS), `Termx` MSI/NSIS (Windows), and AppImage/deb/rpm
(Linux) that contain the existing Python/FastAPI core unchanged in behavior, plus a
thin native host that owns lifecycle, tray/menu, notifications, permissions, and
installers. No backend rewrite.

## Architecture decision

**Tauri 2 shell + PyInstaller onedir Python sidecar, frontend served by the existing
backend.**

Rejected alternatives:

- Electron: larger runtime, no benefit over Tauri here; the UI already communicates
  over HTTP/WS.
- Pure pywebview/BeeWare: no first-class tray/menu/auto-update/installer/signing story.
- Migrating FastAPI to another language: forbidden by the brief.
- PyInstaller onefile: slow per-launch extraction and AV noise; onedir is faster and
  keeps helper binaries as untouched data files.

Why Tauri: system webviews (WKWebView/WebView2/WebKitGTK), Rust lifecycle supervisor,
first-party tray/menu/notification/autostart/updater plugins, and DMG/MSI/NSIS/AppImage/
deb/rpm bundling with documented signing hooks. The remote web UI never receives Tauri
IPC (capabilities scope only the local bootstrap page), so the security boundary stays
exactly where it is today: the authenticated Python server.

### Runtime topology

```
Termx.app / Termx.exe / termx.AppImage
└── Tauri main process (Rust)
    ├── owns single instance, window, menu, tray, notifications, autostart, updater
    ├── picks/adopts a backend port + persisted passcode
    ├── spawns and supervises the sidecar:
    │     Resources/backend/termx-backend --desktop --port N --passcode ... 
    ├── reads structured JSON events from sidecar stdout (ready/notify/log)
    ├── navigates main window to http://127.0.0.1:N/?k=<passcode>
    └── on quit: POST /api/shutdown (loopback) → wait → kill process tree
└── Sidecar: the existing Python app (uvicorn + FastAPI + PTY + capture + tunnels)
    └── children: PTY shells, termx-capture helper, cloudflared/ngrok/tailscale
```

The sidecar binds `0.0.0.0` exactly as today, so LAN phone/browser access and tunnels
keep working; the shell only ever loads it over loopback.

## File plan (smallest change that works)

### Python (functional core — minimal, additive)

| File | Change |
| --- | --- |
| `src/termx/notify.py` | New. `notify(title, body)` writes one JSON event to stdout when `TERMX_DESKTOP=1`; no-op otherwise. The single internal notification interface. |
| `src/termx/hostenv.py` | New. Augment `PATH` for GUI launches (macOS `/etc/paths`, `/etc/paths.d`, Homebrew; Windows common dirs). Called first in `main()`. |
| `src/termx/desktop/paths.py` | Bundled helper lookup (`sys._MEIPASS/helpers/...`), `chmod +x` for bundled POSIX helpers. |
| `src/termx/desktop/permissions.py` | New. macOS `CGPreflightScreenCaptureAccess`/`CGRequestScreenCaptureAccess`, `AXIsProcessTrusted`/`AXIsProcessTrustedWithOptions` via ctypes; portable "unknown" elsewhere. |
| `src/termx/desktop/capabilities.py` | Use real macOS permission statuses in `probe_desktop()`. |
| `src/termx/terminals.py` | New. `PosixTerminal` (moved from `sessions.py`) and `WinTerminal` (pywinpty/ConPTY). |
| `src/termx/sessions.py` | Delegate to `terminals`; Windows-safe defaults (`COMSPEC`/PowerShell); keep public API and POSIX behavior identical. |
| `src/termx/config.py` | Windows-aware `available_shells()`/`default_shell`; no behavior change on POSIX. |
| `src/termx/cli.py` | `--desktop` mode: no banner, resolves bundled web dir, prints `{"termx":"ready",...}` after uvicorn starts, emits ready notification. Existing CLI untouched. |
| `src/termx/app.py` | Additive endpoints: `GET /api/connect` + `GET /api/connect/qr.svg`, `POST /api/notify`, `POST /api/shutdown` (desktop + loopback only), `GET /api/permissions`, `POST /api/permissions/request`; `app:"termx"` in health; tunnel connect/error notifications. |
| `src/termx/static/connect.html` | New pairing page: URLs, passcode, QR image (SVG from the existing `qrcode` dependency). |
| `pyproject.toml` | `pywinpty; sys_platform=='win32'` runtime dep; `packaging` dependency group with PyInstaller. |

### Packaging

| File | Purpose |
| --- | --- |
| `desktop/backend_entry.py` | PyInstaller entrypoint importing `termx.cli.main`. |
| `desktop/termx-backend.spec` | Onedir spec: data = `app/dist`→`web`, `termx/static`, macOS helpers; hidden imports for uvicorn/websockets/qrcode/pywinpty; excludes tkinter/test tools; optional aiortc collection. |
| `desktop/scripts/build_sidecar.py` | Cross-platform orchestrator: export web → PyInstaller → copy to `desktop/src-tauri/resources/backend` → ad-hoc sign on macOS (optional identity). |
| `desktop/scripts/sign_macos_sidecar.sh` | Sign every Mach-O inner→outer with runtime options; entitlements for helpers. |

### Tauri shell

| File | Responsibility |
| --- | --- |
| `desktop/src-tauri/tauri.conf.json` | Product metadata, resource mapping `resources/backend` → `backend/`, DMG/MSI/NSIS/deb/rpm/AppImage, macOS min 13, signing/notarization hooks, updater config disabled by default. |
| `src/main.rs` | Plugin registration, single instance, setup, `RunEvent` close-to-tray, quit drain. |
| `src/backend.rs` | Port selection/adoption, pid/lock file, spawn with `--desktop`, stdout JSON event loop (ready/notify/log), crash detection, bounded auto-restart, graceful stop via `/api/shutdown` then process-tree kill, logs. |
| `src/config.rs` | Per-OS app data/log dirs, persisted `{port, passcode, adopted}`, one-time migration of legacy `~/.config/termx`. |
| `src/menu.rs` | Native app menu (macOS) / window menu: Connection Info, Copy Phone URL, Permissions, Restart Backend, Check for Updates, Launch at Login, Quit; standard Edit menu for webview clipboard. |
| `src/tray.rs` | Tray icon: Show/Hide, Connection Info, Copy URL, Start/Stop Tunnel, Restart Backend, Quit; template icon on macOS. |
| `src/bridge.rs` | HTTP helpers against loopback (health with passcode, permissions status/request, shutdown) using `reqwest` or direct TCP; no third-party API surface. |
| `src/updater.rs` | Optional update check when an endpoint/pubkey is configured. |

### CI

`.github/workflows/desktop.yml`: matrix
- `macos-15` (`aarch64-apple-darwin`) and `macos-15-intel` (`x86_64-apple-darwin`)
- `windows-latest` (NSIS + MSI)
- `ubuntu-22.04` (AppImage + deb + rpm)

Steps: pnpm web export → `uv sync --group packaging` → `build_sidecar.py` → optional
macOS sidecar signing → `tauri build` with signing env passthrough →
`tauri-action` release upload on tags. Cache uv/pnpm/Rust. Existing `ci.yml` gains a
Windows pytest job so the ConPTY path is exercised.

## Interfaces (frozen contracts)

Sidecar stdout (one JSON object per line, only in `--desktop` mode):

```json
{"termx":"ready","port":8787,"urls":["http://127.0.0.1:8787","http://192.168.1.5:8787"],"tunnel":null}
{"termx":"notify","title":"Termx is running","body":"Open http://192.168.1.5:8787 on your phone"}
```

Backend endpoints added:

- `GET /api/connect` (auth) → `{urls, tunnel_url, passcode, qr_svg}`
- `POST /api/notify` (auth) → routes through `termx.notify`
- `POST /api/shutdown` (auth + `TERMX_DESKTOP=1` + loopback client)
- `GET /api/permissions` / `POST /api/permissions/request` (auth)
- health gains `"app":"termx"` for adoption detection.

## Lifecycle state machine (Rust)

`ResolvingPort → Spawning → WaitingReady(≤20s) → Running`
`Running → ExitedUnexpectedly → Restarting (≤3, backoff 1/2/4s) → Running | Failed`
`Running → Stopping (shutdown endpoint → SIGTERM/tree-kill after 5s) → Stopped`

- Duplicate app launches: `tauri-plugin-single-instance` focuses the existing window.
- Stale sidecar: pid file + health check; adopt only when `/api/machine` accepts the
  persisted passcode, otherwise allocate the next free port (`8787..8857`, then `0`).
- Occupied port owned by a foreign service: skip to next free port.
- Uninstall: app-data/log dirs are user data; installer removes app files, shell
  removes pid/lock files on clean exit.

## Permission model after packaging

- The app bundle is the TCC responsible process. `POST /api/permissions/request`
  triggers `CGRequestScreenCaptureAccess()` and
  `AXIsProcessTrustedWithOptions(prompt)` from the sidecar; macOS attributes the prompts
  to `Termx.app` because it launched the sidecar.
- The shell also opens the exact System Settings panes and shows current status.
- Nested Swift helpers are signed inner→outer inside the bundle; the sidecar signs with
  `runtime` + `allow-unsigned-executable-memory` + `disable-library-validation` so
  notarization succeeds.
- On macOS the shell prompts once on first run; "Later" never blocks terminal use.
- Windows/Linux keep existing behavior (Linux portal/compositor prompts still come from
  the compositor).

## Verification matrix

| Area | macOS (local) | Windows | Linux |
| --- | --- | --- | --- |
| Backend sidecar standalone (`--desktop`) | run + curl | CI smoke | CI smoke |
| Installer build | `tauri build` local (ad-hoc) | CI | CI |
| First launch / health / window | local | CI (headless not possible; smoke export) | CI |
| Tray/menu/notifications | manual local | manual | manual |
| Screen capture + Screen Recording | manual local | n/a (unsupported today) | compositor-dependent |
| Accessibility input | manual local | n/a | xdotool/ydotool |
| Tunnelling | manual local (cloudflared) | manual | manual |
| Restart/crash recovery | local (kill sidecar) | manual | manual |
| Upgrade/uninstall | manual | CI artifact | CI artifact |
| pytest | yes | CI | CI |

Hardware/GUI-only checks that cannot run unattended are documented as manual gates in
`desktop/README.md`.

## Implementation status

Delivered and verified locally on macOS (x86_64):

- Python bridge: `--desktop` mode with structured stdout events, port fallback on bind
  conflicts, bundled resource resolution, GUI `PATH` augmentation, notification events,
  `/api/connect`, `/api/notify`, `/api/shutdown` (desktop+loopback), `/api/permissions`,
  cross-platform terminal backend (POSIX + `winpty`/ConPTY), real macOS permission
  status/request.
- Packaging: PyInstaller onedir sidecar built and smoke-tested standalone (health,
  sessions, connect, shutdown, notifications), Tauri 2 shell (single instance, tray,
  native menu, notifications, autostart, updater hooks, permission onboarding, crash
  restart with backoff, stale-process cleanup, process-tree quit), debug and release
  `.app` bundles built and run.
- Lifecycle verified: graceful quit (`/api/shutdown` audit event, pid file removed,
  process tree gone), unexpected sidecar SIGKILL auto-restart, app SIGKILL → stale
  sidecar cleaned on next launch, occupied-port behavior with a foreign server running.
- CI: `.github/workflows/desktop.yml` builds macOS arm64/x86_64, Windows, and Linux
  installers with signing/notarization hooks; `ci.yml` now tests on Windows and macOS.
- UI: responsive layout hook, split terminal panes with focus/tab integration, sheets
  as centered dialogs on wide screens, extra-key bar hidden when a fine pointer and wide
  viewport indicate a hardware keyboard. Verified against the packaged release backend
  with headless Chromium (11/11 checks).
- Cross-platform remote screen/input/displays:
  - macOS: CoreGraphics display enumeration, `termx-capture --display` selection,
    CGVirtualDisplay-backed `termx-virtual-display` (process-held, parent-PID cleanup),
    verified locally for enumeration and live capture; helper binaries are compiled and
    signed by the `macos-helpers` workflow (local CLT Swift lacks the platform path).
  - Windows: GDI `BitBlt` capture with Pillow (bundled via platform marker), `SendInput`
    pointer/keyboard/unicode input, native clipboard, `EnumDisplayMonitors` enumeration
    with driver names; virtual displays require an installed IddCx driver and report
    exact guidance. Covered by `tests/test_capture_windows.py` in the Windows CI matrix.
  - Linux: `xrandr`/Hyprland/Sway/wlr-randr enumeration, grim/maim/ffmpeg/import/scrot
    capture with output/geometry selection, xrandr virtual monitors. Verified in Docker
    with Xvfb and with an Xorg dummy output: enumeration, JPEG capture, and
    `xrandr --setmonitor` create/destroy all passed.
  - Backend/UI: display selection flows through `DesktopManager`, the desktop
    WebSocket (`{"type":"display"}`), `/api/displays`, `useDesktop`, and the display
    dock.
- Branding: robot terminal SVG is the app icon (Expo web/Android/iOS/splash/favicon and
  Tauri `.icns`/`.ico`/PNGs) and is inlined in the bootstrap and pairing pages.


Remaining release-time steps (configuration, not code):

- Provide signing secrets in CI: `APPLE_*`, `WINDOWS_CERTIFICATE*`,
  `TAURI_SIGNING_PRIVATE_KEY`; run the Desktop workflow on a tag and install from the
  produced artifacts.
- Manual GUI matrix on real hardware: tray/menu interactions, native notification
  appearance, first-run permission prompts, screen capture/control after granting TCC,
  tunnel providers, Windows and Linux runtime smoke.

## GitHub-hosted release pipeline

`.github/workflows/desktop.yml` is the only supported release path:

- `workflow_dispatch` (optional `release_tag` + `draft`) or `v*` tag push.
- Four runners: macos-15 (arm64), macos-15-intel (x86_64), windows-latest (MSI + NSIS),
  ubuntu-22.04 (deb/rpm/AppImage).
- Per runner: pnpm web export → `uv sync` + pytest gate → PyInstaller sidecar (signed
  when secrets exist) → fresh Swift helper build on macOS → `tauri build` → release
  upload via `tauri-action`; installers also stay as 14-day workflow artifacts.
- Signing is optional and secret-driven: `APPLE_CERTIFICATE`,
  `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `KEYCHAIN_PASSWORD`, and
  notarization via `APPLE_ID`/`APPLE_PASSWORD`/`APPLE_TEAM_ID` or
  `APPLE_API_KEY_ID`/`APPLE_API_ISSUER`/`APPLE_API_KEY_P8`; Windows via
  `WINDOWS_CERTIFICATE`/`WINDOWS_CERTIFICATE_PASSWORD`. Unsigned runs still publish.
- Tag builds fail if the tag does not match `desktop/src-tauri/tauri.conf.json`.
- `permissions: contents: write` is declared so `GITHUB_TOKEN` can create releases.
- Intel macOS builds are skippable per run (`include_intel`), unsigned builds are
  ad-hoc signed on macOS and still published, and `SHA256SUMS-<platform>.txt` files are
  attached to each release.
- Post-build signing scripts: `desktop/scripts/sign_macos_release.sh` (sign + notarize
  + staple + verify `.app`/`.dmg`, ad-hoc fallback), `sign_windows_release.ps1`
  (signtool with base64 or `.pfx`), `sign_linux_release.sh` (GPG detached signatures,
  optional deb/rpm metadata signing).


