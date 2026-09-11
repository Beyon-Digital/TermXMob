# Product UI specification: remote machine control

## Outcome and audience

Termx users run a host process on a macOS 13+ or Linux (Wayland and X11) machine and connect from the Expo iOS/Android app or the same web UI served by Python. They need to treat the phone or browser as a real workspace for that machine: a reliable terminal, saved one-tap commands, machine-owned shell/directory defaults, start/stop tunnels while connected, and full desktop viewing plus input, including using the client as a separate virtual display when the host can create one.

Success signal: after pairing, a user can resume a machine, interrupt a running command with Ctrl+C, hold backspace to delete, run a saved command with one tap, change the default shell, start a Cloudflare/ngrok/Tailscale tunnel from the UI, switch Terminal/Desktop, control a physical display, and — when capabilities allow — create a virtual display matching the client.

Non-goals for this specification: App Store distribution of privileged macOS drivers, Windows hosts, translating the remote OS accessibility tree, multi-user simultaneous control ownership beyond one controller plus view-only observers, and shipping Expo Go as the remote-screen client.

## Existing product evidence

- Platforms: Expo Router 57 app in `app/src/app/` (iOS, Android, web) plus FastAPI host in `src/termx/`. Python serves `app/dist` when exported, otherwise `src/termx/static/index.html`.
- Routes: `/` connect (`index.tsx`), `/workspace` terminal, `/settings` client themes, `/scan` QR. Stack, header hidden, fade animation (`_layout.tsx`).
- Tokens: `app/src/lib/themes.ts` `ThemeUi` + `TerminalTheme`; persist selected/custom themes in `app/src/lib/storage.ts`. Starter tokens in `app/src/constants/theme.ts` are unused by the live connect/workspace path.
- Terminal: native WebView embed `src/termx/static/embed.html`; web uses `@xterm/xterm`. PTY WebSocket in `src/termx/app.py` / `app/src/hooks/use-pty.ts`. Extra keys in `extra-keys.tsx` have no backspace/interrupt/repeat.
- Sessions: `src/termx/sessions.py` always uses `$SHELL` or `/bin/zsh`, `cwd=Path.home()`, `start_new_session=True` without `TIOCSCTTY`. Kill uses SIGHUP/SIGKILL. No host-stored preferences.
- Auth: shared passcode (`src/termx/auth.py`), CORS `allow_origins=["*"]`, passcode also in `?k=` WebSocket URLs.
- Tunnels: CLI `--tunnel` starts one Cloudflare Quick Tunnel (`src/termx/tunnel.py`); no runtime API.
- Native UI: `@expo/ui` `Host`/`Button`/`TextInput` on Home; workspace is RN `Pressable`. `@expo/ui` exports universal `BottomSheet` (`isPresented`) and `@expo/ui/community/bottom-sheet`.
- Tests: `uv run pytest` on `tests/`. No frontend test script. Lint: `pnpm lint`. Types: `pnpm exec tsc --noEmit`.

## Journey contract

| Step | User intent | UI state | Primary action | Exit / error |
| --- | --- | --- | --- | --- |
| Launch Home | See machines and how to add one | Skeleton, then cards or empty | Resume latest eligible machine or Scan/Add | Invalid 127.0.0.1 on device; unreachable host; wrong passcode |
| Add / scan | Pair to a host | Address + optional passcode; camera on Scan | Connect | Parse error; passcode required; network hint |
| Capability fetch | Know what this host can do | Workspace shell with banners for missing helpers | Continue to Terminal even if Desktop is gated | Setup sheet for helper/permissions; Terminal remains usable |
| Terminal work | Run a shell | Session tabs, xterm, extra keys | Type, interrupt, hold-delete, new/kill session | Reconnect banner; session exit refreshes list |
| Run command | Execute a saved command now | Commands sheet, search + list | Tap row (or confirm if flagged) | Host save error; Run disabled offline; sheet keeps draft |
| Set defaults | Persist shell and cwd on the machine | Defaults sheet with pickers and resolved preview | Save | Invalid shell/path field errors; existing sessions unchanged |
| Manage tunnel | Expose or hide this machine | Tunnel sheet with provider status | Start / Stop / Restart | Missing binary/auth; stop-current-route confirm; switch to LAN if possible |
| Open Desktop | See and use the machine GUI | Stream canvas, view-only first | Enable control; pick display | Permission, encoder, ICE/media failure with recovery |
| Use as display | Add the client as extra screen | Display dock with virtual option | Create virtual display for this device | Unsupported compositor/API shows reason, never fake success |
| Leave | Stop controlling without destroying host state | Confirm only if stopping last route or in-use virtual display | Back to Home | Disconnect does not kill PTYs or virtual displays unless chosen |

## Screen and component contract

| Surface | Content hierarchy | Interactions | Data / state | Reuse |
| --- | --- | --- | --- | --- |
| Home `/` | Wordmark, one-line purpose, last/recent machine cards (identity, route, last seen), add-machine card, Scan, Settings | Resume, Forget, Connect, Scan QR | `listConnections`, `fetchHealth` → machine snapshot | Replace stacked `@expo/ui` demo form; keep parse/save helpers |
| Scan `/scan` | Camera, Cancel, error | Scan Termx URL | Existing camera + parse flow | Keep route |
| Workspace shell `/workspace` | Top: machine title, connection/tunnel chip, Terminal/Desktop switch, overflow (Commands, Defaults, Tunnel, Settings, Machines) | Mode switch preserves both session and stream | `getCurrentConnection`, health/capabilities | Refactor `workspace.tsx`; `SessionTabs` stays Terminal-only |
| Terminal | Tabs, xterm, extra keys including INT/BKSP/CLR | Ctrl+C / INT → SIGINT; BKSP hold repeat; CLR = `^U` | PTY WS + `signal` messages | `TerminalView`, `usePty`, `ExtraKeys` |
| Commands sheet | Search, New, saved rows (name + command preview), manage | One tap runs `command + \n`; confirm if `confirm`; edit/delete | Host `/api/commands` | `@expo/ui` `BottomSheet` |
| Defaults sheet | Shell picker from host list, cwd field + resolved preview, save | Validate then PUT preferences | `/api/preferences`, `/api/machine` | Same sheet primitive |
| Tunnel sheet | Provider cards, profile, status, URL, logs disclosure, Start/Stop/Restart | Serialized mutations; handoff warning | `/api/tunnels*` | Same sheet primitive |
| Desktop canvas | Letterboxed stream, overlays for display name/latency/control, view-only shield | Touch/trackpad, keyboard, wheel, clipboard consent, full screen | Media WS (v1) or WebRTC when `capabilities.webrtc` | New `desktop-view` |
| Display dock | Horizontal thumbnails: physical, virtual, Create | Select, create, destroy with confirm | `/api/displays` | Bottom safe-area bar, Terminal keys hidden |
| Settings `/settings` | Appearance (existing themes), then links/sections for defaults, remote control, tunnels, security that open sheets or in-page groups | Theme remains client-local | Theme storage + host APIs when connected | Extend, do not replace theme editor |
| Host setup | Checklist: helper version, screen, input, virtual output | Open OS settings, re-check | `/api/permissions` | Sheet or full-screen from capability banner |

## Responsive and platform behavior

- Mobile portrait: Home is a single-column dashboard. Workspace uses a compact top bar; extra keys or display dock occupy the bottom safe area, never together. Sheets are native bottom sheets (`@expo/ui` `BottomSheet`, snap `half`/`full`).
- Mobile landscape / tablet: stream or terminal uses width; dock becomes a shorter overlay; 44pt targets preserved.
- Web narrow: same hierarchy; BottomSheet uses vaul. Hardware keyboard focuses xterm or desktop surface.
- Web wide (≥900px): Home max-width ~720–880 centered. Workspace may show a thin left identity rail; canvas stays dominant. Drawers still used for commands/tunnels so mobile/web share the contract.
- Keyboard: software keyboard pushes Terminal via existing `KeyboardAvoidingView`. Desktop mode hides extra keys. Backgrounding pauses capture send; resume renegotiates.
- Back: sheet dismiss → exit full screen → Home. Android back does not kill sessions.
- Deep link: existing `termx:` / HTTP QR. Passcode query kept for QR pairing only; subsequent API calls use header.

## Accessibility contract

- Every control has a name, role, and selected/busy/disabled state. Extra keys keep `keyA11yLabel`; INT = “Interrupt”, BKSP = “Backspace”, CLR = “Clear line”.
- Focus order follows visual order. Dismissing a sheet restores terminal or desktop focus.
- Status uses icon + text, not color alone. Contrast follows theme tokens; custom themes remain the user’s responsibility but built-ins must stay readable.
- Touch targets ≥44pt. Hold-to-repeat is cancellable and has a one-shot press equivalent. Reduced motion: no auto-dim animation; sheets still present.
- Remote desktop chrome is accessible; the streamed OS is not claimed as an accessible local UI. Always provide a non-gesture `Exit control` / `Machines` control.
- Errors are field-level plus a short summary. Do not announce raw provider logs unless Details is expanded.

## Protocol and data contract

### Machine config (`~/.config/termx/config.json`)

Versioned JSON, atomic replace, mode `0600` for sibling `secrets.json`.

```json
{
  "version": 1,
  "terminal": { "shell": "/bin/zsh", "cwd": "/Users/name" },
  "commands": [{ "id": "…", "name": "Git status", "command": "git status", "confirm": false, "order": 0, "created_at": 0, "updated_at": 0 }],
  "tunnels": { "active_profile_id": null, "profiles": [] },
  "desktop": { "view_only_default": true, "retain_virtual_display": false }
}
```

Secrets (provider tokens) never appear in GET responses; fields return `configured: true`.

### HTTP (all except `/api/health` require current auth)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | `ok`, `version`, `passcode_required`, `hostname`, `os`, `capabilities`, tunnel summary |
| GET | `/api/machine` | hostname, os, arch, screens, shells, default cwd, helper versions, permissions |
| GET/PUT | `/api/preferences` | `{ shell, cwd }`; PUT validates |
| GET/POST | `/api/commands` | list / create |
| PATCH/DELETE | `/api/commands/{id}` | update / delete; POST list reorder via `{ "order": ["id"] }` |
| GET | `/api/tunnels` | providers detected, profiles (redacted), runtime status |
| POST | `/api/tunnels/profiles` | create profile |
| PATCH/DELETE | `/api/tunnels/profiles/{id}` | update / delete |
| POST | `/api/tunnels/start` | `{ profile_id }` |
| POST | `/api/tunnels/stop` | stop active; `force` after confirm |
| GET | `/api/displays` | physical + virtual |
| POST | `/api/displays/virtual` | `{ width, height, dpr, refresh_hz }` |
| DELETE | `/api/displays/{id}` | destroy virtual only |
| WS | `/api/sessions/{id}/pty` | existing + `{ "type": "signal", "name": "int" \| "term" \| "hup" }` |
| WS | `/api/desktop/session` | media v1: JSON control + binary JPEG frames; input events |

Capabilities object:

```json
{
  "saved_commands": true,
  "session_defaults": true,
  "dynamic_tunnels": true,
  "remote_screen": false,
  "virtual_display": false,
  "webrtc": false,
  "providers": { "cloudflare": true, "ngrok": false, "tailscale": false }
}
```

`remote_screen` is true only after a working capture+input backend probe. `virtual_display` is true only when an adapter actually created or can create an output. `webrtc` stays false until the native helper encoder lands; clients must use the media WebSocket when it is false. This is a transport phase, not a product-scope change.

### Session create

`POST /api/sessions` accepts optional `shell` and `cwd`. Omitted values use machine preferences. Snapshot includes `shell`, `cwd`, `argv`.

### PTY

Child must `setsid` + `TIOCSCTTY` on the slave so ISIG delivers `SIGINT` on `0x03`. Extra INT key and `{type:"signal","name":"int"}` call `killpg(SIGINT)`. `name:"term"` is SIGTERM and is not bound to Ctrl+C.

### Desktop input (media WS JSON)

`pointer` (move/down/up/wheel, normalized 0–1 plus display id), `key` (down/up, text, modifiers), `clipboard` (get/set with consent), `control` (`view_only` | `control`), `release_all`. Server maps to macOS CGEvent / Linux portal, xdotool, or uinput according to probe.

### Tunnel providers

Shared `TunnelProvider`: `id`, `detect()`, `start(profile, port)`, `stop()`, `status()`. Profiles: Cloudflare `quick` | `named`, ngrok `http`, Tailscale `serve` | `funnel`. State: `stopped | starting | connected | stopping | error`. Mutations serialize. Stopping the route the client is on requires handoff or confirm.

## Mobbin evidence

| Pattern studied | Source link | Useful principle | Adaptation for this product |
| --- | --- | --- | --- |
| Stream-first remote canvas | [Grok Bot](https://mobbin.com/screens/675e9d35-b189-4efc-a8af-b02b91ff2205) | Desktop fills the canvas; modes are secondary chrome | Termx Desktop letterboxes the stream; trackpad vs direct touch is a dock toggle, not a covering panel |
| Thumb-reach device remote | [Google TV](https://mobbin.com/screens/bf58e46c-b8e0-4e5c-aae1-56f74bcb7a70) | Persistent identity + large bottom controls around a gesture surface | Replace media keys with keyboard, trackpad, display, view-only; keep machine chip visible |
| Saved actions sheet | [Fabric](https://mobbin.com/screens/4f0f6cdd-ddb2-4b6f-9d83-454cdddb0fb0) | Search and create sit above one-tap rows | Commands sheet: search, New, then immediate-run rows with optional confirm |
| Defaults grouped form | [Obsidian](https://mobbin.com/screens/7f043fcb-4094-43ad-b377-e88ca4aa74ee) | Short labels, compact pickers, helper text | Shell and cwd as grouped rows with resolved path preview |
| Connection status first | [NordVPN](https://mobbin.com/screens/4866f93d-811c-4bc1-a4b4-b4daf178f860) | Status is the hero; disconnect is secondary | Tunnel sheet leads with provider state and URL; Stop is emphasized only when connected |
| Machine inventory density | [Tailscale web](https://mobbin.com/screens/4e6b5e40-1d4d-4b51-971f-49dfdd49a5e1) | Identity, address, version, last seen | Web Home uses this density; mobile uses cards instead of a tight table |
| Device/home empty vs populated | [Dropbox](https://mobbin.com/screens/538dbf3c-b36e-4f5f-9008-58b2ca693880) | Clear empty prompt vs list of known targets | Home empty state teaches Scan/LAN IP; populated state leads with resume |

## Acceptance criteria

- [ ] Home shows empty, saved, connecting, error, and 127.0.0.1-on-device states without the previous stacked demo layout.
- [ ] Connecting from Home or Scan still lands in `/workspace` with at least one live PTY.
- [ ] New sessions use host-saved shell and cwd; invalid values fail with field errors and do not spawn a bogus PTY.
- [ ] Saved commands persist on the host and run with one tap (`command` + newline); `confirm` requires a second explicit action.
- [ ] Commands, defaults, and tunnel UIs use `@expo/ui` bottom sheets on iOS/Android and the web drawer fallback.
- [ ] Cloudflare, ngrok, and Tailscale each have a provider adapter; UI can start/stop/restart a detected provider; missing binaries show install guidance.
- [ ] Stopping the client’s current tunnel either switches to a healthy alternate URL or asks for confirmation; no orphan provider processes remain after stop or host exit.
- [ ] Ctrl+C / INT delivers `SIGINT` to the PTY foreground group; a trapping child receives it; the UI does not merely echo `^C`.
- [ ] BKSP sends `0x7f`; hold repeats after ≥400ms, accelerates, and stops on release/blur/unmount/disconnect; CLR sends `0x15`.
- [ ] Workspace offers Terminal and Desktop modes; Desktop starts view-only; control requires an explicit toggle and shows a revoke path.
- [ ] Physical displays can be listed and selected when `remote_screen` is true; pointer, keys, and wheel map to the selected display.
- [ ] Virtual display create/destroy is offered only when `virtual_display` is true; unsupported hosts show the compositor/API reason.
- [ ] Disconnecting from the app does not kill PTYs; virtual displays follow the saved retain policy.
- [ ] `/api/health` reports capabilities used to gate Desktop/tunnel actions; Terminal remains available when screen capabilities are false.
- [ ] `uv run pytest`, `pnpm exec tsc --noEmit`, and `pnpm lint` pass for touched code.

## Open questions

None. Transport phasing (`webrtc: false` until the helper encoder exists, media WebSocket meanwhile) is an implementation detail inside the approved Desktop journey, not an unresolved product choice.
