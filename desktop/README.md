# Termx desktop

Native host for the existing Python/FastAPI Termx core. The desktop app does not
reimplement the backend: it starts the packaged Python sidecar, supervises it, and adds
native lifecycle, tray/menu, notifications, permissions onboarding, and installers.

```
Termx.app / Termx.exe / termx.AppImage
└── Tauri shell (Rust)
    ├── chooses/adopts a backend port and retains native bootstrap authority
    ├── spawns Resources/backend/termx-backend --desktop --port N --passcode ...
    ├── reads JSON events from sidecar stdout (ready / notify)
    ├── loads http://127.0.0.1:N/ with the dedicated workspace UI
    ├── native origin-checked bridge signs in and holds access tokens in Rust memory
    ├── OS credential storage holds rotating refresh tokens scoped to host identity
    └── on quit: POST /api/shutdown → wait → kill process tree
```

The workspace URL contains no passcode or JWT. The bootstrap credential is
available only to the native shell for an unconfigured local host; managed sign-in
never falls back to it after a permission denial. Password and completed OIDC
sign-in become the same managed session. A shared native bridge serializes refresh
rotation across workspace windows, stores refresh secrets in the OS credential
store, and uses HttpOnly access cookies for authenticated sockets. Detaching a
window opens a credential-free session/panel URL against the same backend.

## Layout

| Path | Purpose |
| --- | --- |
| `bootstrap/` | Tiny loading page shown until the backend is ready |
| `backend_entry.py` | PyInstaller entrypoint |
| `termx-backend.spec` | Onedir spec (web export, static files, macOS helpers) |
| `workspace/` | Dedicated desktop/browser React workspace; Expo remains mobile |
| `runtime/` | Pinned language-server packages and release runtime provenance |
| `scripts/prepare_runtime.py` | Stage pinned Chromium, verified js-debug, and language assets |
| `scripts/build_sidecar.py` | Package the workspace/runtime with Python → stage/sign for Tauri |
| `scripts/sign_macos_sidecar.sh` | Sign every Mach-O in the sidecar (inner→outer) |
| `src-tauri/` | Rust shell |

## Releasing from GitHub Actions

`.github/workflows/desktop.yml` builds and publishes Rust delivery binaries and
installers on GitHub. Source checks and isolated Python/container tests run locally;
Rust delivery binaries are built only in CI.

Trigger options:

1. **Tag (recommended):** bump the version, push a tag, and the workflow publishes a
   release.

   ```bash
   # keep these four in sync
   #   desktop/src-tauri/tauri.conf.json  "version"
   #   desktop/src-tauri/Cargo.toml       package.version
   #   pyproject.toml                     project.version
   #   src/termx/__init__.py              __version__
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

`macos-15-intel` is the Intel label configured in this workflow. If the account
cannot use that runner, run with **include_intel = false** while arranging an
available Intel CI runner for the same target. Delivery builds remain in CI.

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

## Workspace development and release packaging

The dedicated UI is built from `desktop/workspace`, with the same generated bundle served to native desktop and external browsers. `desktop/scripts/update_web_ui.sh` builds this workspace; it never clones or modifies the mobile Expo repository.

```bash
pnpm --dir desktop/workspace install --frozen-lockfile
pnpm --dir desktop/workspace test
pnpm --dir desktop/workspace build
uv run termx --host 127.0.0.1 --port 8787
```

Rust delivery binaries and installers are built only through `.github/workflows/desktop.yml`. That workflow installs the frozen Python development/media/webrtc extras, prepares matching Chromium and pinned js-debug/language assets, then packages `desktop/workspace/dist` into the sidecar. Its frozen runtime smoke check must pass before the installer is published. Source checking with `cargo check` is separate from a delivery build.

The sidecar stage lives in `desktop/src-tauri/resources/backend/` (gitignored). See [runtime/README.md](runtime/README.md) for package versions, integrity, host prerequisites and remaining platform verification.

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

- Screen capture runs only while someone is actively watching. It starts when the
  first viewer connects, pauses when every viewer is hidden/inactive (`pause`/`resume`
  messages; `TERMX_CAPTURE_PAUSE_GRACE`, default 1s) and is released
  `TERMX_CAPTURE_IDLE_GRACE` seconds (default 5) after the last one disconnects. On
  macOS this stops the helper, the ScreenCaptureKit stream and its recording indicator;
  on Windows/Linux frame capture and input pumps stop the same way. Nothing is captured,
  streamed, or kept in memory when the Desktop view is closed or the app quits.
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

## Linux window capture and recording dependencies

Exact application-window capture requires an X11 session, `wmctrl` for enumeration, ImageMagick `import` for exact-window pixels and `xdotool` for scoped input. Debian/RPM installers declare these runtime packages. AppImage users need to install them through their distribution package manager. A normal desktop window manager supplies EWMH window metadata; CI starts Openbox inside Xvfb and captures only a uniquely named `xmessage` fixture created by the test. CI-only fixture packages are `xvfb`, `openbox` and `x11-apps`.

A pure Wayland session requires a portal adapter and explicit OS consent for window capture. The current X11 adapter reports that boundary and never substitutes a full-desktop crop. macOS exact-window capture requires Screen Recording permission on the actual device. Headless Chromium accessibility tests do not require that native capture permission.

Native managed authentication also requires an unlocked OS credential service: macOS Keychain, Windows Credential Manager, or a Linux Secret Service on the user's D-Bus session (for example GNOME Keyring). Missing secure storage fails authentication closed. CI verifies a uniquely named synthetic entry with write/read/delete cleanup; Linux starts an isolated `dbus-run-session` and GNOME Keyring using temporary fixture storage. This proof covers the platform credential API, while installer GUI, OS permission prompts, and actual device workflows remain separate checks.

The frozen sidecar's `--runtime-smoke` qualification executes its packaged Node and FFmpeg, initializes and shuts down all five bundled language servers, initializes/disconnects bundled debugpy, and renders a data-only page with bundled Chromium. It saves `desktop/build/runtime-smoke.json`; finding asset files alone does not satisfy this check. CI separately runs actual JavaScript/TypeScript breakpoint and language-navigation tests after preparing pinned assets, with missing runtime prerequisites treated as failures.

Installed native GUI qualification uses `desktop/scripts/native_gui_smoke.py` with the unchanged executable installed from the generated Linux DEB or Windows MSI. The pinned `tauri-driver` delegates to the real WebKitWebDriver or a Microsoft Edge driver matching the installed WebView2 runtime. It exercises the real owner-setup form, cookie-cleared restart through the OS credential store, session revocation/logout, detached-window reopening and missing-monitor recovery. Reports and native webview screenshots are uploaded as `workspace-native-gui-*`; the presence of this harness or a source test alone is not a successful GUI qualification. macOS native GUI and native OIDC redirects remain separate direct-verification gates.

macOS packaging copies the complete backend runtime into `Resources/backend` through Tauri's `bundle.macOS.files` directory copier, preserving the Chromium app/framework symbolic links. The normal resource file copier dereferences those links and cannot safely package a complete Chromium framework. CI verifies and signs the staged browser bundle before signing the enclosing application; a compile check alone does not qualify this packaging path.

For a portable or isolated installation, `TERMX_DESKTOP_DATA_DIR` selects a dedicated absolute native data directory (configuration and logs are stored below it). It never imports another profile's legacy tokens automatically. Unix application directories/files use owner-only permissions; Windows directories and the bootstrap configuration receive protected owner/SYSTEM/administrator DACLs before credential reads or writes. Foreign-owned storage and links/reparse points are refused. The native GUI fixture uses a fresh directory plus isolated webview state; it leaves `HOME` and the user's existing accounts unchanged. Linux CI provides a private D-Bus/GNOME credential service and an Xvfb/Openbox display for this fixture.

Microphone recording starts from the explicit Record action and stops when its chat is hidden or unmounted. macOS bundles declare `NSMicrophoneUsageDescription` and the hardened-runtime audio-input entitlement; the WKWebView delegate requests native consent only for audio from the main frame at the current workspace origin and denies cameras, foreign frames and stale host ports. This preserves OS/TCC consent and the original Wry dialog delegate. Linux connects WebKitGTK's actual user-media permission request to a native, default-No microphone dialog, rechecking the host origin before allowing capture. A working system audio device and PulseAudio/PipeWire stack are required. Windows WebView2 denies foreign-origin microphone requests and camera requests; its default native microphone prompt and Windows privacy settings decide requests from the current workspace. SSO redirects remain supported. Controlled web browsing runs in the separate managed browser. Native policy tests and bundle settings do not prove that a signed, installed application can record on a real audio device; that remains a separate platform/device qualification.
