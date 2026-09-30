---
name: testing-termx
description: How to run the TermXMob FastAPI daemon and end-to-end test its web UIs — daemon startup, passcode/pairing flows, the bundled terminal UI, the Expo web UI, API/CORS/WebSocket auth probing, signal paths, the webrtc capability flag, and the execution-sandbox backends (linux-ns / windows-token).
---

# Testing TermXMob (FastAPI daemon + web UIs)

## Run the daemon

`uv` lives at `~/.local/bin/uv` (add to PATH). `uv sync` may already be done
(`.venv` exists); Python >= 3.14 is required and uv manages it.

```sh
cd ~/repos/TermXMob
export PATH="$HOME/.local/bin:$PATH"
TERMX_CONFIG_DIR="$HOME/.config/termx-dev" nohup uv run termx --host 127.0.0.1 --port 8787 --passcode test-only > /tmp/termx-server.log 2>&1 &
```

- Always set `TERMX_CONFIG_DIR` to a scratch dir so test state (tokens, config) never touches the real `~/.config/termx`.
- The banner prints `local http://127.0.0.1:8787?k=test-only` plus a LAN URL and a QR code. Omit `--passcode` for a passcode-off daemon (`passcode off` in the banner).
- Health check: `curl -s http://127.0.0.1:8787/api/health`.
- Start extra instances on other ports (`--port 8797`) with env vars like `TERMX_CORS_ORIGINS` to test config variants side by side.

### Windows host notes (Git Bash on the Windows box)

- **PYTHONUTF8=1 is required** (or `PYTHONIOENCODING=utf-8`): on a cp1252 console the
  startup banner's `qr_ascii` block characters raise `UnicodeEncodeError` inside
  `print_banner`, which kills the whole `_serve` task — the daemon exits right
  after binding. `PYTHONUTF8=1 uv run termx ...` is the workaround.
- Git Bash mangles `/flag` args into paths: use `cmd //c "whoami /groups"` or
  `whoami //groups`, not `whoami /groups`.
- The venv python (`.venv/Scripts/python.exe`) is a Windows binary — it cannot read
  Git Bash `/tmp`; pipe `curl | .venv/Scripts/python.exe -c ...` or use Windows paths.
- `uv` is at `/c/Users/Administrator/.local/bin/uv`; repo is `C:\Users\Administrator\repos\TermXMob`.
- Chrome-for-testing lives at `/c/devin/chrome/chrome-win64/chrome`; Edge is also installed.
- `seclogon` (Secondary Logon service) must be RUNNING for the `windows` sandbox
  backend probe (CreateProcessWithTokenW). `sc.exe query seclogon`.
- Windows task/profile paths: pass `cwd` with forward slashes (`C:/Users/...`) in JSON bodies.

## Which UI to test

- `http://127.0.0.1:8787/` serves the Expo app from `desktop/web/` (heavier).
- `http://127.0.0.1:8787/_/app.html` serves the bundled terminal UI
  (`src/termx/static/index.html`) — simplest deterministic surface.
- With no passcode the `/_/app.html` page auto-passes the gate, auto-creates a
  `/bin/zsh -l` session and attaches its WS — a prompt appears with no clicks.

## Web UI + passcode flow (Expo app)

- `http://127.0.0.1:8787/?k=<passcode>` auto-connects: the bundle parses `k` into `connection.passcode` and sends it as `X-Termx-Passcode` on `/api/*`. A correct `k` lands you in `/terminal` with live sessions; a wrong `k` leaves you on the "Add a machine" screen (the machine card may still show `online` — that only means `/api/health` is reachable unauthenticated, NOT that auth passed).
- "Open workspace" on a machine with a wrong stored passcode shows "Connecting…" then reverts with no error — the gate holds but feedback is silent.
- `127.0.0.1:8787` and `localhost:8787` are different origins → separate localStorage; handy for re-testing fresh-connect flows without clearing site data.

## Driving the terminal UI

- Click the black terminal area once to focus xterm's hidden textarea before
  typing; keystrokes are sent as `{type:"input"}` WS messages.
- The key bar at the bottom has `ESC TAB CTRL ALT ^C BKSP` / arrows / `CLR / -`.
  In Chrome fullscreen at 1024x768 the `^C` button is at ~(802, 694); buttons
  stretch with window width, so verify position with a `zoom` into the key-bar
  region before clicking — they respond to `pointerdown`, so a normal click works.

## Adversarial signal testing (important)

The `^C` button (`data-key="INT"`) sends BOTH `{type:"signal","name":"int"}` AND
a raw `\x03` byte as input (`static/index.html` `applyExtraKey`). With the tty's
default `isig`, the `\x03` alone makes the line discipline SIGINT the foreground
pgrp — so `sleep 60` + `^C` would pass even with a broken WS-signal path.

To isolate the WS-signal path, run the job with isig disabled inside a child
shell so the `\x03` byte is inert input:

```
sh -c 'stty -isig; stty -a; sleep 60'
```

The on-screen `stty -a` output shows `-isig` in `lflags` as recorded proof. Then
click `^C`: the prompt returns in ~1s iff `send_signal` correctly targets
`os.tcgetpgrp(master_fd)`; the queued `\x03` visibly echoes as a literal `^C`
glyph at the *new* prompt (and may pollute the next typed command, e.g.
`command not found: ^Cecho` — cosmetic, just retype). On a broken implementation
the prompt stays blocked for the full 60s. Restore default with `stty isig`.

## API probes worth reusing

- Health: `curl -s http://127.0.0.1:8787/api/health` — unauthenticated minimal payload (no hostname/os/tunnel since the API-surface hardening).
- Machine: `-H 'X-Termx-Passcode: test-only'` → full snapshot incl. hostname/os/tunnel and `capabilities.execution_sandbox` (per-profile sandbox report: backend, strength, granted/grantable, network_control, filesystem_isolation, identity_isolation, resource_limits).
- Pair: `curl -X POST .../api/pair -H 'X-Termx-Passcode: test-only'` → `{token, scopes}`; verify the token works via `Authorization: Bearer <token>` on `/api/machine`.
- CORS preflight: `curl -i -X OPTIONS .../api/health -H "Origin: <o>" -H "Access-Control-Request-Method: GET"` → allowed origins (localhost + private LAN by default; `TERMX_CORS_ORIGINS` overrides) get `200` + `access-control-allow-origin` echo; rejected get `400` and no ACAO.
- The passcode is the admin credential → every scope (incl. `ai-settings`, `agent-run`).
  Bearer tokens from /api/pair are least-privilege.

## Execution sandbox end-to-end (linux-ns / windows-token)

Restricted profiles `workspace`/`agent` resolve to a kernel backend when the
platform probe succeeds (`/api/machine` → `capabilities.execution_sandbox.backend`
reports `linux-ns`, `windows-token`, or `windows-user`; `host` means no sandbox
selected — a silent `host` for agent/workspace is a regression).

The only end-user path that spawns under a restricted profile is the **agent
run_shell/run_check funnel** (interactive terminal sessions are host-profile via
PTY; runbooks launched from the API run `host`; the agent-invoked runbook tool
uses the task's sandbox profile). To exercise it:

1. Provider key via env only — start the daemon with
   `TERMX_AI_OPENROUTER_API_KEY=$OPEN_ROUTER` in its env (never the API body/files),
   then `PUT /api/agent/providers/openrouter` with
   `{id:"openrouter", kind:"openai-compatible", base_url:"https://openrouter.ai/api/v1",
   model:"openrouter/free", capabilities:["shell"]}` — `secret_configured:true`.
   OpenRouter serves the OpenAI **Responses** API (`/responses`) and free models do
   emit `function_call` items.
2. `POST /api/conversations {title,cwd,mode:"agent",provider_id}` then
   `POST /api/agent/tasks {prompt:"Use the run_shell tool to run exactly: whoami /groups",
   cwd, provider_id, mode:"agent", conversation_id}`.
3. Expect TWO approval pauses (`status:"awaiting_approval"`): a `kind:"plan"` gate,
   then a `kind:"tool"` gate. Approve each via
   `POST /api/agent/tasks/{task_id}/approvals/{approval_id} {"decision":"approved"}`.
   NOTE the policy engine treats any token starting with `/` as a path —
   `whoami /groups` is gated as "Outside selected project" on every platform
   (Windows `/`-flags always trip it).
4. Poll `GET /api/agent/tasks/{task_id}` until `completed`; the `process.output`
   events carry the streamed stdout.

Platform-specific proof:
- **windows-token**: child `whoami /groups` ends with
  `Mandatory Label\Low Mandatory Level  Label  S-1-16-4096`; host shows
  `High Mandatory Level  S-1-16-12288`. The FS boundary is `icacls
  /setintegritylevel L` on workspace/writable roots — writes outside are
  kernel-denied. `network_control:false` is honest (no unprivileged primitive).
- **windows-user** (provisioned `termx-sandbox` account — `/api/machine`
  reports `backend:"windows-user"`, `strength:"restricted-user"`,
  `identity_isolation:true`): strongest single proof is `whoami` →
  `devinbox\termx-sandbox` (token mode would read the host user) plus
  `whoami /groups` → Low Mandatory Level and NO `BUILTIN\Administrators`.
  Write inside workspace exits 0 — the file is owned by `DEVINBOX\termx-sandbox`
  (DACL grant), write outside → `Access is denied.` exit 1.
  Stale csrss-owned `cmd.exe 0xc0000142` dialogs may litter the VM desktop
  after provisioning/session recycle — they are NOT spawned by the sandbox:
  attribute them by watching hwnd churn while a direct `runner_for("agent")`
  spawn runs clean, rather than dismissing them.
- **linux-ns**: probe via `id -u`/`cat /proc/self/uid_map` or blocked writes —
  see test_sandbox suites for boundary proofs.

## WebSocket auth probing

`websockets` is in the dev venv (`websocket-client` is not). Caveat: `websocket.close(code)` **before** `accept()` surfaces to the client as `HTTP 403`, so auth-fail (4401) and session-not-found (4404) are indistinguishable. To prove auth passed, attach to a REAL session id (grab one from a connected UI's URL, e.g. `/terminal/<id>`) — a good passcode upgrades the socket and streams live PTY bytes (there is no explicit welcome/CONNECTED marker; replay bytes may arrive first), while no-auth/wrong-passcode get `HTTP 403`.

## capabilities.webrtc flag

- Reflects `RtcManager.available()` — `false` unless aiortc is installed.
- To exercise the `true` branch: `uv run --extra webrtc termx --port 8798 ...` (resolves the `webrtc` extra from pyproject: aiortc+av+numpy; ~30s download). `/api/health` should report `webrtc: true`. `/api/machine` only reports the live flag since the API-surface hardening (machine_snapshot is fed `rtc.available()`); on older builds it stays `false` even with aiortc installed, so treat `/api/health` as the authoritative probe there. Revert with plain `uv sync --dev`.

## Browser automation on this Mac box

- The managed `browser` target is usually unavailable. Launch Chrome yourself:
  `open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/termx-test-chrome --no-first-run`
  then use `computer` target=browser `cdp_port=9222` for `query`/`inspect`/`act`. `browser_console` and the plain `browser` target refuse to work against it, but DOM inspect/act do.
- macOS: resize the window with osascript (`set position/size of window 1`), don't rely on wmctrl.
- JS `confirm()` dialogs (e.g. "Forget machine?") block browser input — dismiss them with a `key Return` action.
- On the Windows box the managed browser IS available (computer tool); Chrome-for-testing
  is at `/c/devin/chrome/chrome-win64/chrome`.

## Devin secrets needed

- `OPEN_ROUTER` — only for agent/LLM-driven flows (execution-sandbox e2e, real
  provider turns). Map to `TERMX_AI_OPENROUTER_API_KEY` in the daemon env;
  use `openrouter/free` (or another free tool-calling model) — never persist it.
- None for the UI/API/auth flows (no tunnel, no AI provider; a throwaway
  `--passcode` is enough).
