# Termx desktop

Native host for the existing Python/FastAPI Termx core. The desktop app does not
reimplement the backend: it starts the packaged Python sidecar, supervises it, and adds
native lifecycle, tray/menu, notifications, permissions onboarding, and installers.

```
Termx.app / Termx.exe / termx.AppImage
└── Tauri shell (Rust)
    ├── chooses/adopts a backend port and passcode
    ├── spawns Resources/backend/termx-backend --desktop --port N --passcode ...
    ├── reads JSON events from sidecar stdout (ready / notify)
    ├── loads http://127.0.0.1:N/?k=<passcode> in the system webview
    └── on quit: POST /api/shutdown → wait → kill process tree
```

## Layout

| Path | Purpose |
| --- | --- |
| `bootstrap/` | Tiny loading page shown until the backend is ready |
| `backend_entry.py` | PyInstaller entrypoint |
| `termx-backend.spec` | Onedir spec (web export, static files, macOS helpers) |
| `scripts/build_sidecar.py` | Export web → PyInstaller → stage/sign for Tauri |
| `scripts/sign_macos_sidecar.sh` | Sign every Mach-O in the sidecar (inner→outer) |
| `src-tauri/` | Rust shell |

## Releasing from GitHub Actions

`.github/workflows/desktop.yml` builds and publishes installers on GitHub — nothing is
built or uploaded from a local machine.

Trigger options:

1. **Tag (recommended):** bump the version, push a tag, and the workflow publishes a
   release.

   ```bash
   # keep these three in sync
   #   desktop/src-tauri/tauri.conf.json  "version"
   #   pyproject.toml                     project.version
   #   app/app.json                       expo.version
   git tag v0.1.0
   git push origin v0.1.0
   ```

   The workflow fails fast if the tag does not match `tauri.conf.json`.

2. **Manual run:** Actions → **Desktop release** → *Run workflow*, optionally enter a
   `release_tag` (e.g. `v0.1.0`) and choose whether the release is a draft. With no tag
   it only uploads workflow artifacts (14-day retention).

Each matrix job also attaches its installers to the release:

| Runner | Output |
| --- | --- |
| `macos-15` (arm64) | `Termx_<version>_aarch64.dmg` |
| `macos-15-intel` (x86_64) | `Termx_<version>_x64.dmg` |
| `windows-latest` | `.msi` (WiX) and `-setup.exe` (NSIS) |
| `ubuntu-22.04` | `.deb`, `.rpm`, `.AppImage` |

Unsigned builds still succeed and publish; add the secrets below to sign and notarize.

### macOS runners: what `macos-15-intel` means

GitHub runs each macOS architecture on a different machine, and the Python sidecar
cannot be cross-compiled (PyInstaller must run on the target CPU). The workflow
therefore uses two runners:

- `macos-15` → Apple Silicon (M-series) → `Termx_<version>_aarch64.dmg`
- `macos-15-intel` → Intel (x86_64) → `Termx_<version>_x64.dmg`

`macos-15-intel` is GitHub's supported Intel label (available through August 2027).
If your plan or org does not provide it, run the workflow with **include_intel =
false**; Apple Silicon users are unaffected and Intel users can build from source.
`macos-26-intel` is a newer alternative label if you want the latest image.

Windows (`windows-latest`) and Linux (`ubuntu-22.04`) run on x64, which is what
virtually all desktop users download.

### Signing scripts

The scripts take no arguments. They read `desktop/signing.env` (copy
`desktop/signing.env.example`), auto-discover the newest artifacts, sign, verify, and
print the result. Run them from the repository root on the matching OS:

```bash
# macOS — newest .dmg (or .app) from the build output, ./dist, ., ~/Downloads
cp desktop/signing.env.example desktop/signing.env   # once, edit identity/notary
desktop/scripts/sign_macos_release.sh

# macOS — ad-hoc sign in place so it runs on this Mac
desktop/scripts/sign_macos_release.sh --in-place

# Linux — GPG detached signatures (+ deb/rpm metadata when tooling is present)
desktop/scripts/sign_linux_release.sh
```

```powershell
# Windows — newest .msi and NSIS setup .exe
.\desktop\scripts\sign_windows_release.ps1
# If script execution is blocked:
#   powershell -ExecutionPolicy Bypass -File .\desktop\scripts\sign_windows_release.ps1
```

Useful extras for every script: `--dry-run` (list what would be signed),
`--all` (sign every discovered artifact), `--input PATH` (search a specific file or
directory, e.g. `~/Downloads`). On macOS, `--identity`, `--keychain`, and
`--notary-profile` override the config file.

When notarization credentials are absent the macOS script still signs and prints the
exact `notarytool`/`stapler` commands to run later. Each release also attaches
`SHA256SUMS-<platform>.txt`.

### Installing unsigned builds

Unsigned releases are fully functional; the OS just asks for confirmation:

| OS | First run |
| --- | --- |
| macOS | Right-click → **Open** → **Open**, or `xattr -dr com.apple.quarantine /Applications/Termx.app`, or Privacy & Security → **Open Anyway**. Ad-hoc builds may re-prompt for Screen Recording/Accessibility after each update. |
| Windows | SmartScreen → **More info** → **Run anyway**, or `Unblock-File .\Termx_*_x64-setup.exe`. |
| Linux | `chmod +x Termx*.AppImage && ./Termx*.AppImage` (no FUSE: `--appimage-extract` first), `sudo apt install ./Termx_*.deb`, or `sudo dnf install --nogpgcheck ./Termx-*.rpm`. |

Verify downloads with the attached checksums:
`shasum -a 256 -c SHA256SUMS-macos-arm64.txt` (or `sha256sum -c`).

### Signing secrets

**Fast path:** `desktop/scripts/push_signing_secrets.sh` validates your certificate and
API key and uploads every secret with `gh secret set` (use `--dry-run` to preview).
The full checklist, Apple setup steps, rotation, and the offline alternative live in
[`SIGNING.md`](SIGNING.md).

Repository → Settings → Secrets and variables → Actions → **New repository secret** (manual alternative):

**macOS (sign + notarize, no Apple ID or password)**

Signing uses a **Developer ID Application** certificate; notarization uses an
**App Store Connect API key** — scoped, revocable, and not usable to sign in to your
Apple account. Neither is your Apple ID/password.

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE` | base64 of your **Developer ID Application** `.p12` |
| `APPLE_CERTIFICATE_PASSWORD` | password set when exporting the `.p12` |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_API_KEY_ID` | 10-character Key ID from the API key |
| `APPLE_API_ISSUER` | Issuer ID (UUID) shown next to the keys |
| `APPLE_API_KEY_P8` | **contents** of `AuthKey_XXXX.p8` (downloaded once, cannot be re-downloaded) |
| `KEYCHAIN_PASSWORD` | any random string (temporary CI keychain; has a default if omitted) |

How to create them:

1. **Developer ID certificate** — Xcode → Settings → Accounts → your team →
   *Manage Certificates* → **+** → *Developer ID Application* (requires a paid team).
   Right-click the certificate in Keychain Access → *Export* → save as `.p12` with a
   password. Then: `base64 -i DeveloperID.p12 | pbcopy` → `APPLE_CERTIFICATE`;
   the password → `APPLE_CERTIFICATE_PASSWORD`; the certificate name →
   `APPLE_SIGNING_IDENTITY`.
2. **App Store Connect API key** — appstoreconnect.apple.com → *Users and Access* →
   *Integrations* → *Team Keys* → **Generate** (role *Developer* or *Admin*).
   Copy the **Key ID** and **Issuer ID**, download the `.p8` **once**, and paste its
   contents into `APPLE_API_KEY_P8`. Revoke the key any time from the same page.

Without macOS secrets the build still publishes, ad-hoc signed.

### Keeping Apple credentials off GitHub entirely

If you would rather not put even the certificate/api key in repository secrets, sign on
your own Mac instead:

```bash
# 1) one-time: install the Developer ID certificate in your login keychain
#    (Xcode -> Settings -> Accounts -> Manage Certificates -> + Developer ID Application)
# 2) one-time: store notarization credentials in your keychain (API key or Apple ID,
#    entered here only)
desktop/scripts/setup_macos_notary.sh            # or --key/--key-id/--issuer
# 3) sign + notarize + staple the downloaded DMG (identity auto-detected)
desktop/scripts/sign_macos_release.sh --input ~/Downloads
```

CI keeps publishing unsigned (macOS ad-hoc signed) builds; the signed DMG is produced
locally. The same pattern applies to Windows (`sign_windows_release.ps1` with a local
`.pfx`) and Linux (`sign_linux_release.sh` with your GPG key).

**Windows (code signing)**

| Secret | Value |
| --- | --- |
| `WINDOWS_CERTIFICATE` | base64 of the code-signing `.pfx` |
| `WINDOWS_CERTIFICATE_PASSWORD` | `.pfx` password |

The same certificate signs the Tauri installers and the bundled `termx-backend.exe`
(via `signtool`, located automatically on the runner).

**Optional (auto-update artifacts)**

`TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, plus
`bundle.createUpdaterArtifacts: true` and a `plugins.updater` endpoint/pubkey in
`tauri.conf.json`. Not needed for plain releases.

**Linux**: direct `.deb`/`.rpm`/`.AppImage` downloads do not need signing. Repository
signing (`dpkg-sig` / `rpm --addsign`) can be added later with a GPG secret; it is not
required to publish.

### Repository settings checklist

- Actions enabled for the repository.
- Settings → Actions → General → Workflow permissions: **Read and write** (the
  workflow also declares `permissions: contents: write`, but an org policy can
  override it).
- GitHub-hosted runners; `macos-15-intel` is required for the Intel DMG — if it is not
  available on your plan, remove that matrix entry and keep arm64 + Windows + Linux.
- The `macos-helpers` workflow (push to `main` touching `helpers/macos/**`) rebuilds and
  commits signed helper binaries; the release workflow also builds them fresh on the
  runner, so releases never depend on that commit.

## Local build

Prerequisites: Rust, `uv`, and PyInstaller via the packaging group.
The web UI is prebuilt in `desktop/web` (committed); refresh it from the private
client repo with `desktop/scripts/update_web_ui.sh` when the client changes.

```bash
uv sync --group packaging
uv run --group packaging python desktop/scripts/build_sidecar.py

cd desktop/src-tauri
cargo tauri build                 # release installers
cargo tauri build --debug --bundles app   # fast local .app for testing
```

The sidecar stage lives in `desktop/src-tauri/resources/backend/` (gitignored).

## Platform artifacts

| OS | Command | Output |
| --- | --- | --- |
| macOS | `cargo tauri build` | `.app` + `.dmg` in `target/release/bundle/` |
| Windows | `cargo tauri build` | MSI (WiX) + NSIS `.exe` |
| Linux | `cargo tauri build` | `.deb`, `.rpm`, `.AppImage` |

CI (`.github/workflows/desktop.yml`) builds arm64 + x86_64 macOS, Windows, and Linux on
tag pushes (`v*`) and manual dispatch, uploading installers as artifacts or a draft
release.

## Signing and notarization

Everything is environment-driven; no application changes are required.

macOS:

1. `TERMX_MACOS_SIGN_IDENTITY` (used by `build_sidecar.py` to sign the Python sidecar
   and bundled Swift helpers) — e.g. `Developer ID Application: Example (TEAMID)`.
2. Tauri signs with `APPLE_SIGNING_IDENTITY` and notarizes with the App Store Connect
   API key (`APPLE_API_KEY_ID`, `APPLE_API_ISSUER`, `APPLE_API_KEY_PATH`). No Apple ID
   or password is used.

Windows:

- `TERMX_WINDOWS_PFX` + `TERMX_WINDOWS_PFX_PASSWORD` (or `WINDOWS_CERTIFICATE` base64 +
  `WINDOWS_CERTIFICATE_PASSWORD`) sign the sidecar executables with `signtool`.
- Tauri reads `WINDOWS_CERTIFICATE`/`WINDOWS_CERTIFICATE_PASSWORD` for the installer and
  app binaries.

Linux: deb/rpm signing is a packaging step (`dpkg-sig`/`rpm --addsign`); artifacts are
produced unsigned by default and can be signed in CI without code changes.

Updates: set `plugins.updater.endpoints`/`pubkey` in `tauri.conf.json` and build with
`createUpdaterArtifacts: true` plus `TAURI_SIGNING_PRIVATE_KEY`. The **Check for
Updates…** menu item uses the configured endpoint and reports cleanly when absent.

## Permissions (macOS)

Screen Recording and Accessibility are attributed to the *responsible* app, which after
installation is `Termx.app`. On first launch the shell offers onboarding; the backend
reports status via `GET /api/permissions` and triggers prompts via
`POST /api/permissions/request` (`CGRequestScreenCaptureAccess` and
`AXIsProcessTrustedWithOptions`). Menu: **Termx → Permissions…**.

- Screen Recording: needed for desktop streaming (`termx-capture`, `screencapture`,
  `ffmpeg`).
- Accessibility: needed to inject pointer/keyboard input.
- The Swift helpers are signed inside the bundle; the sidecar is signed with
  `allow-unsigned-executable-memory` and `disable-library-validation` so notarization
  succeeds.

Tunnels and capture fallbacks discover binaries from `PATH`; the Python core augments
`PATH` (`src/termx/hostenv.py`) because Finder launches start with a minimal
environment.

## Remote screen, input, and displays

`GET /api/displays` and the desktop WebSocket (`{"type":"display","id":...}`) expose
per-platform display enumeration and selection. The stream captures the selected
output.

| Platform | Enumeration | Capture | Input / clipboard | Virtual output |
| --- | --- | --- | --- | --- |
| macOS 13+ | CoreGraphics (`CGGetActiveDisplayList`) | `termx-capture --display <id>`; `screencapture -D`, `ffmpeg avfoundation` fallbacks | `CGEventPost`; `pbpaste`/`pbcopy` | `termx-virtual-display` signed helper (private `CGVirtualDisplay`, process-held), BetterDisplay, deskpad |
| Windows 10+ | `EnumDisplayMonitors` (+ driver name via `EnumDisplayDevices`) | GDI `BitBlt` + Pillow JPEG (+ cursor); `ffmpeg gdigrab` fallback | `SendInput` absolute pointer/keys/unicode; native clipboard | user-space creation is impossible; install an IddCx driver and its outputs appear as displays |
| Linux X11 | `xrandr --listmonitors` | `ffmpeg x11grab`, `import -crop`, `maim -g`, `scrot`, `xwd`+`convert` | `xdotool`; `xclip`/`wl-copy` | `xrandr --setmonitor` (needs at least one output) |
| Linux Wayland | `hyprctl -j`, `swaymsg -t get_outputs`, `wlr-randr --json` | `grim -o <output>`, `ffmpeg` PipeWire/x11grab | `xdotool` (XWayland) / `ydotool`; `wl-copy` | Hyprland/Sway/GNOME/KDE adapters |

`TERMX_CAPTURE_BIN` / `TERMX_VIRTUAL_DISPLAY_BIN` override helper discovery on any
platform. Helper updates ship via `.github/workflows/macos-helpers.yml` (signed builds
are committed from CI).

### Platform tests

- `tests/test_displays.py` — enumeration and xrandr parsing (every OS).
- `tests/test_capture.py` — probe + helper protocol with a fake helper.
- `tests/test_capture_windows.py` — real GDI capture, `SendInput` probe, clipboard
  roundtrip (runs in the Windows CI matrix with Pillow installed).
- `tests/test_capture_linux.py` — real capture and `xrandr --setmonitor` roundtrip; CI
  runs it under `xvfb-run` (`linux-desktop` job) and it passes on an Xorg dummy output.
- macOS helper code compiles and is exercised in the `macos-helpers` workflow; live
  capture needs the Screen Recording grant, which CI runners cannot provide.

## Lifecycle behavior

- Screen capture runs only while someone is watching: the `termx-capture` helper (and
  its ScreenCaptureKit stream / TCC indicator) starts when the first desktop viewer
  connects and is released `TERMX_CAPTURE_IDLE_GRACE` seconds (default 5) after the last
  one disconnects. Nothing is captured, streamed, or kept in memory when the Desktop
  view is closed or the app quits.
- Single instance: a second launch focuses the existing window.
- Closing the window hides to the tray; quit from the menu/tray/Cmd+Q.
- Port 8787 is preferred; a live Termx on it with the stored passcode is adopted,
  otherwise the next free port is chosen. The backend also falls back across ports if a
  bind race occurs.
- A stale `backend.pid` left by a crashed app is cleaned up on the next launch.
- If the sidecar exits unexpectedly, the shell restarts it up to three times with
  backoff and notifies; after that, **Restart Backend** is available in menu/tray.
- Quit sends `POST /api/shutdown` (loopback + desktop only), waits, then kills the
  process tree/tree-kills on Windows.

## Config, logs, and data

| OS | Config | Logs |
| --- | --- | --- |
| macOS | `~/Library/Application Support/com.jaexxxy.termx/config` | `~/Library/Logs/com.jaexxxy.termx` |
| Windows | `%APPDATA%\com.jaexxxy.termx\config` | `%LOCALAPPDATA%\com.jaexxxy.termx\logs` |
| Linux | `$XDG_DATA_HOME/com.jaexxxy.termx/config` | `$XDG_DATA_HOME/com.jaexxxy.termx/logs` |

Existing `~/.config/termx` files (`config.json`, `tokens.json`, `audit.jsonl`) are
copied once into the new config directory.

## Manual verification checklist

Hardware- and GUI-only paths that CI cannot assert:

- First launch: loading page → workspace loads and auto-connects.
- Tray icon/menu, dock menu, Cmd+Q quit, close-to-tray, launch-at-login toggle.
- Native notifications: backend ready (hidden start), tunnel connected, `/api/notify`.
- macOS: grant Screen Recording/Accessibility, restart, verify live desktop view and
  input; virtual display creation; helper arch matching on Apple Silicon/Intel.
- Tunnels: `cloudflared`/`ngrok`/`tailscale` start, URL notification, stop/restart.
- Crash/restart: kill `termx-backend`, confirm automatic recovery; kill the app,
  relaunch, confirm stale sidecar cleanup.
- Upgrade/uninstall through the produced installers; user data remains in the config
  directory.
