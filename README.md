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

> **SECURITY**: the passcode is the only gate on a full remote-shell bridge —
> anyone holding it gets unrestricted shell and filesystem access to the host.
> Run the daemon on trusted LANs only; do not expose it to untrusted networks,
> and treat `--tunnel` as publishing that shell to the internet.

`--tunnel` needs [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).

The process prints LAN URLs, an optional Cloudflare URL, and a QR code. Open the URL or scan the QR. Sessions keep running if you disconnect.

## Desktop app

Build a native desktop host that bundles the Python server, the web UI, and the macOS
helpers. End users install the artifact for their OS and need no Python or tooling.

```bash
uv sync --group packaging
uv run --group packaging python desktop/scripts/build_sidecar.py   # packages python + desktop/web
cd desktop/src-tauri && cargo tauri build
```

The web UI ships prebuilt in `desktop/web`; refresh it from the private client repo with
`desktop/scripts/update_web_ui.sh`.

Installers (`.dmg`, MSI/NSIS, `.deb`/`.rpm`/`.AppImage`) are produced by
`.github/workflows/desktop.yml`. Push a `vX.Y.Z` tag that matches the app version (or
run the workflow manually) and GitHub builds, signs, and publishes the release; see
[`desktop/README.md`](desktop/README.md) for signing ([`desktop/SIGNING.md`](desktop/SIGNING.md)),
permissions onboarding, lifecycle, and log locations.

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

## Client apps

The mobile (Expo) and web client lives in the private repository
`Psyborgs-git/termx-app`; it is not part of this host repository. A prebuilt web
bundle is committed under `desktop/web` so the desktop app and `uv run termx` serve
the full UI without any Node tooling. Rebuild it with
`desktop/scripts/update_web_ui.sh` after client changes.

## Agent runtime and ACP

The Python backend supports internal provider runs and ACP v1 subprocesses.
Use Settings → Engines → ACP Registry to detect, install, and register agents
on the host, then run `uv sync` and `uv run termx`. The ACP SDK is pinned in
`pyproject.toml`; each native session keeps its own process.
Browser disconnection does not stop a running task. A packaged desktop host
must be rebuilt to include backend changes; restarting an older binary does
not update its bundled Python code.

Internal runs default to **256 tool calls and four hours**. Limits are checked
between provider turns, after the returned tool batch completes. Exhaustion
preserves history/results and creates a `budget` approval with status
`awaiting_approval`, instead of failing the task. Approving it continues the
same task with an additional step allowance and a fresh time window; completed
tools are not replayed. Budget approvals survive host restarts. Cancel or deny
the approval to end the task. Subagents retain smaller independent budgets.

Host defaults live under `agent` in `~/.config/termx/config.json` (or
`$TERMX_CONFIG_DIR/config.json`). Merge this example into the existing file;
keep the other settings:

```json
{
  "agent": {
    "limits": {
      "max_steps": 2000,
      "max_seconds": 86400,
      "shell_timeout_s": 1800,
      "max_parallel_subagents": 3,
      "max_subagents_total": 8
    },
    "provider_timeout_s": 600,
    "engines": {
      "devin": {"startup_timeout_s": 30, "cancel_timeout_s": 5},
      "grok": {"startup_timeout_s": 30, "cancel_timeout_s": 5}
    }
  }
}
```

Internal limits resolve in order: task override, custom-agent limits, host
defaults. `max_steps` accepts 1–100000, `max_seconds` 1–604800, and
`shell_timeout_s` 1–86400. Invalid settings are rejected with an error rather
than silently capped. `provider_timeout_s` controls HTTP request/read-idle
timeouts (1–86400 seconds); it is independent of the task budget.

ACP launch configuration also accepts `executable` (absolute CLI path),
`model`, `mode`, and `config_options` (option ID → string/boolean). Discover
the native IDs before setting them; different agents/accounts offer different
choices. Optional `env_names` restricts inheritance to runtime environment
variables plus those named variables. Without it, the existing host
environment is inherited. TermX passcode/token variables are always removed.
Keep credentials in the host environment or the CLI's own credential store.
No secret values belong in `config.json` or agent Markdown.

The API is `/graphql`, authenticated with the paired token or host passcode.
Field names use snake_case. These operations expose the actual native state:

```graphql
query {
  agent_configuration
  engine_configuration(engine_id: "devin", cwd: "/absolute/project/path")
  engine_models(engine_id: "devin")
}

mutation($input: AgentTaskInput!) {
  create_agent_task(input: $input) { id status model mode engine_session_id }
}
```

### ACP registry and custom connections

TermX reads the [official ACP registry](https://github.com/agentclientprotocol/registry)
from its published index. At startup it checks for registry agents already
available on the host and discovers their ACP capabilities in the background.
Settings → Engines → ACP Registry offers one action to install and register
an agent, or to register an already detected executable. Registry distributions
use their declared npm, uv, or platform binary package; downloaded binaries
are SHA-256 checked when the registry provides a digest. Installations and the
registry snapshot are kept under `~/.termx/acp`. The host must have `npm` for
npm packages or `uv` for Python tools. Sign in after installation using only
the authentication methods the agent advertises.

Devin and Grok keep their existing TermX engine IDs while using registry
distribution metadata. Antigravity is registry-managed as well. The native
Codex app-server and Claude SDK adapters remain on their dedicated agent
harnesses because those integrations are not ACP clients. Any ACP v1 stdio
agent missing from the registry can still be added through the collapsed
Custom ACP connection form, or the host-admin `update_agent_configuration`
mutation:

```json
{
  "acp_runners": {
    "my-agent": {
      "label": "My ACP agent",
      "transport": "stdio",
      "executable": "/path/to/my-agent",
      "args": ["--acp"],
      "env_names": ["MY_AGENT_API_KEY"],
      "enabled": true,
      "cwd": "/path/to/discovery/project",
      "catalogue_timeout_s": 120
    }
  },
  "engines": {
    "my-agent": {"config_options": {"<advertised-option-id>": "<advertised-value>"}}
  }
}
```

Custom runner IDs are stable lowercase identifiers. Arguments are passed
directly without a shell. Environment names select values already present on
the host; secret values are never sent to the app. The executable must
implement ACP v1 over standard newline-delimited JSON-RPC stdio. Streamable
HTTP is not exposed by this ACP client. No `--version` CLI is required: agents
report their version through `initialize.agentInfo`.

Registry metadata and agent capabilities are cached in the background at app
startup. Reopening a screen does not spawn agents or refetch the registry.
`refresh_acp_registry` refreshes registry metadata; `refresh_engine_catalogue`
refreshes model, mode, and reasoning choices. Concurrent runner refreshes are
single-flight. For isolated test hosts, `TERMX_ENGINE_STARTUP_REFRESH=0`
disables startup discovery.

```graphql
query {
  acp_registry
}

mutation {
  refresh_acp_registry
  install_acp_runner(registry_id: "antigravity-acp")
  refresh_engine_catalogue(engine_id: "my-agent", cwd: "/path/to/project")
}
```

The host-admin install mutation installs and registers an agent in one
operation. `acp_registry` returns cached metadata without launching agents.
Omit `engine_id` to refresh all runners. The catalogue mutation returns descriptors,
configurations, timestamps and failure/stale state. The app exposes global
and individual refresh buttons. Discovery creates temporary sessions without
prompts, reads the agent's selectors, and inspects each advertised model's
dependent reasoning options. It never invents model or effort IDs. A failed
refresh retains previous choices with an error; a failed model branch hides
unavailable dependent selectors. `catalogue_timeout_s` (up to 3600 seconds)
bounds the entire discovery; startup/cancel timeouts bound individual calls.

Discovery uses the configured discovery folder or the last manually supplied
folder. `engine_task_configuration` reads the actual running session instead.
ACP validates selections again when creating or resuming a task. Refreshes
never restart or reconfigure live tasks. Stop active tasks before removing or
disabling their custom runner. `authenticate_engine` accepts only an agent's
advertised authentication method and initiates login on the host when selected
by the user; discovery never initiates login.

For ACP task variables, supply `engine`, `model`, `engine_mode`, and
`config_options` using the advertised values. `engine_mode` is the native mode
ID and is optional when the agent has no mode selector. TermX chat `mode`
never implies an ACP mode ID; internal tasks use `mode: "ask"` or `"agent"`. Native modes have
agent-defined permissions and are not a TermX read-only sandbox guarantee.
For example, if discovery returns those exact IDs:

```json
{
  "input": {
    "prompt": "Implement and verify the requested change",
    "cwd": "/absolute/project/path",
    "engine": "devin",
    "model": "<advertised-model-id>",
    "engine_mode": "<advertised-mode-id>",
    "config_options": {},
    "conversation_id": "<conversation-id>"
  }
}
```

Follow-up tasks with the same `conversation_id` reuse the native session.
Explicit selections are applied before the new turn; omitted selections retain
the current session configuration. `engine_task_configuration(task_id: "…")`
returns the live options and values. Unsupported/invalid selectors and a
second concurrent turn are rejected. ACP config options take precedence over
legacy modes, which are used only when no config options are advertised.
ACP prompts have no arbitrary client duration timeout; cancellation waits for
the agent and closes its process if it fails to stop within `cancel_timeout_s`.
Internal run limits are not translated into undocumented ACP parameters:
native agents own their own token/turn budgets and report native stop reasons.
An agent must advertise `session/load` for restart recovery; a failed resume
returns an error instead of silently starting an empty session.
An in-flight native turn fails with a resumable-session message after a host
restart; the next task can load that session. TermX does not automatically
replay an interrupted native prompt or its side effects.

Custom-agent Markdown can persist `x-termx.engine`, `x-termx.model`,
`x-termx.engine_mode`, `x-termx.config_options`, and `x-termx.limits`. The
GraphQL `CustomAgentInput`/`CustomAgentPatchInput` support the same fields.
Omitting a task's `engine` uses its custom-agent engine, then the conversation's
existing native session, then `internal`. Explicit `engine: "internal"` selects
the internal runtime. Launch settings remain host-owned.

To change host defaults at runtime, use the host-admin mutation:

```graphql
mutation($input: JSON!) {
  update_agent_configuration(input: $input)
}
```

`limits` updates merge with saved limits; an `engines` object replaces that map.
ACP launch changes affect new processes, provider timeout changes affect new
HTTP clients, and task limit changes affect new tasks. Restart the host after
editing the file directly or changing a non-ACP executable path.

To renew an internal budget with an explicit total step limit:

```graphql
mutation($task: String!, $approval: String!) {
  resolve_agent_approval(task_id: $task, approval_id: $approval,
    input: {decision: "approved", limits: {max_steps: 4000, max_seconds: 86400}})
}
```

`max_steps` must exceed the already completed tool count. Existing failed tasks
keep their historical status; the new pause/continue behavior applies to runs
executed by the upgraded backend.

The adapter follows the official [ACP session configuration contract](https://agentclientprotocol.com/protocol/v1/session-config-options)
using the [Python SDK](https://agentclientprotocol.com/libraries/python).

### Sign in with ChatGPT

In **Settings → AI & Models**, choose **Continue with ChatGPT**. Complete the
browser sign-in on the connected host computer (the callback is a host loopback
listener). If the host cannot open a browser, copy the sign-in link and open it
on that same computer. No OpenAI API key or client secret is required.

After authorizing eligible ChatGPT plan usage, choose the saved ChatGPT account
and an available model from the chat model menu. These providers power the
**TermX internal** engine, including Ask and Agent tasks. Native Codex, Claude,
and ACP engines keep their own authentication; this flow does not import or
replace a Codex CLI login. Model availability, plan eligibility, shared usage
limits and credits are controlled by OpenAI. The first successful sign-in shows
a plan-usage confirmation; **Manage usage** opens ChatGPT's usage settings.
TermX never automatically switches a failed ChatGPT request to API-key billing.

The host uses OpenAI's documented OSS dynamic registration, PKCE S256 and OIDC
identity verification. Each verified account/client pair remains separate even
when emails match. The stable host ID and account mappings are stored in
`chatgpt-accounts.json` with owner-only permissions; access, refresh and retained
ID tokens stay in the OS credential store (Keychain, Secret Service, or Windows
DPAPI). Secure credential storage must be available before starting sign-in.
Token refresh is serialized across daemon processes sharing the config directory,
and replacement credentials are saved before use. Sign out stops that account's
active internal tasks, attempts session revocation and clears local tokens while
retaining the registration for later sign-in. If revocation cannot be confirmed,
the app directs users to disconnect TermX in ChatGPT settings.

GraphQL controls (require `ai-settings`): `start_chatgpt_sign_in(account_id)`,
`chatgpt_sign_in_status(attempt_id)`, `cancel_chatgpt_sign_in(attempt_id)`,
`chatgpt_accounts`, `refresh_chatgpt_models(account_id)`,
`acknowledge_chatgpt_plan(account_id)`, and `sign_out_chatgpt(account_id)`.
Authorization attempts expire after ten minutes and are bound to the initiating
TermX credential. Tokens, PKCE verifiers and callback codes are never returned
through GraphQL. The account model catalog is refreshed after sign-in; settings
also offers **Refresh models**. Requests use streamed Responses with `store=false`,
local transcript history and namespaced function tools. ChatGPT plan usage does
not enable the native computer-use tool in this implementation.

Official contract: [registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
[accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
and [models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).


## Host notes

- Config, tokens, and audit logs live in `~/.config/termx` (or `$TERMX_CONFIG_DIR`).
- `POST /api/pair` with the passcode returns a Bearer token; passcode still works for QR.
- CI: `.github/workflows/ci.yml` runs pytest on Linux/macOS/Windows plus a Linux
  desktop capture job. `.github/workflows/macos-helpers.yml` builds Swift helpers on `macos-15` for **arm64 and x86_64**, lipos a universal binary, and commits all of them to `helpers/macos/bin/` on `main`. Termx selects the slice that matches this machine. Sign locally with Command Line Tools (no Xcode.app):
  `cp helpers/macos/signing/signing.env.example helpers/macos/signing/signing.env && helpers/macos/sign.sh`
  Put a `.p12` path in `signing.env` to import into the persisted `helpers/macos/signing/termx.keychain-db` (gitignored).
- Desktop capture prefers that signed `termx-capture`, then `ffmpeg`, then `screencapture`/`grim`/`maim`. Grant Screen Recording on macOS.
- Virtual extra displays: Hyprland `hyprctl output create headless`, Sway `create_output`, X11 `xrandr --setmonitor`, or `termx-virtual-display` on PATH. GNOME/KDE only if `gdctl`/`kscreen-doctor` help lists virtual/create.
- Tunnels: `cloudflared`, `ngrok`, or `tailscale` on `PATH`. Named Cloudflare tunnels need a token on the profile.
- Screen capture only runs while a desktop viewer is actively watching. Hidden tabs,
  minimized/hidden windows and backgrounded phones send `pause`, so capture and the
  screen-recording indicator stop everywhere (macOS helper, Windows GDI, Linux tools),
  and `resume` restarts on demand. Disconnecting releases capture after
  `TERMX_CAPTURE_IDLE_GRACE` (default 5s); pausing uses `TERMX_CAPTURE_PAUSE_GRACE`
  (default 1s).
- Stop (`Ctrl+C` / SIGTERM) drains tunnels, desktop pumps, virtual displays, then PTY process groups.
- `TERMX_CORS_ORIGINS` optional comma-separated list of explicit origins; when unset, CORS defaults to localhost + private-LAN origins only.

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
