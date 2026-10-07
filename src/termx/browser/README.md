# Managed browser and action review

The host launches separate Chromium profile directories and serves authenticated
JPEG frame streams, semantic context and input through the same desktop/web UI.
No external tab attachment, cookie import, CDP endpoint or agent arbitrary
JavaScript is exposed. The processes share the trusted host OS account; hostile
tenant OS isolation requires an external deployment boundary.

Human viewers share an in-flight capture per tab. Pending controls have priority,
and each viewer independently rechecks live authorization before capture and
before delivery. Capture leases are checked again after rendering; private-mode
or takeover changes discard the old frame. SQL-backed socket checks run in a
worker so a large session projection cannot block the browser control loop.

## Enforcement matrix

| Execution path | Enforced interception | Restricted browser handoff |
| --- | --- | --- |
| Internal TermX Agent | Registry browser tools and managed mutating tools use the durable broker; typed project writes can use an evaluated reviewer. Unknown process/computer/delegation effects require exact human review. | Supported. Only broker tools and scoped typed project file tools are available while granted; shell, computer, Git/process/runbook/subagent escape paths are rejected. |
| Claude coding | Mandatory SDK PreToolUse and completion/failure hooks cover built-in and MCP tools even when native allowed_tools would otherwise bypass a prompt. Typed Write/Edit proposals can use the reviewer. | Separate browser workflow supported. Native `tools=[]`, empty setting sources/skills and strict MCP configuration expose only the host's in-process browser MCP and wait tool. |
| Codex coding | Managed emitted command/file/permission approval requests use the broker, single-use permits and item completion. Native approval routing is `user`, policy `untrusted`. | Refused. Native sandbox fast paths remain native policy; no universal host pre-tool hook or verified mechanism to remove every alternate tool is available. |
| ACP coding | Host file read/write and terminal callbacks use the broker; runner permission requests require allow_once and settle on reported tool completion. | Refused. Runner-owned operations depend on its permission cooperation; capability advertisement alone cannot prove alternate tools are absent. |
| Scheduled native tasks without delegated managed-session authority | Existing native permission behavior; no interactive user's managed session is borrowed. | Refused until explicit scoped task delegation exists. |

Gateway `set_browser_service` attaches the reviewer to existing and future
dynamic ACP adapters. Task authority is written by the workspace before worker
dispatch and never accepted from model arguments. Reviewer input contains hashes,
host-derived effects and a redacted bounded task summary. The reviewer gets no
tools, raw input values, cookies, private page observations or browser control.

Allow rules are bounded by owner, managed session, task, tool, project, target,
grant and policy version. Unknown and sensitive effects cannot acquire a blanket
allow. Policy changes, takeover, private mode, expiry, document/target changes and
restart invalidate stale permits. Consumption is persisted before side effects;
unknown outcomes are never automatically replayed.

## Recordings

Browser tabs and native windows require explicit capture/record consent. Plain
typed text is excluded; named parameters store placeholders and ephemeral replay
values never enter records. Private mode stops recording and blocks model
observation. A privacy epoch also discards a computer frame when private mode
entered and exited while capture was in flight.

Window capture uses exact CoreGraphics window IDs on macOS, PrintWindow on
Windows, and X11 window capture on Linux. It never substitutes a desktop crop.
Wayland needs an explicit portal adapter and is reported unavailable. Native
input checks that the selected owner/window is foreground. Host screen capture
and accessibility permissions remain necessary.

Draft editors preserve executable JSON steps. A separate human-selected safe
tab/window, explicit replay consent and current session authority are required
for a real dry-run. Activation checks the exact draft/steps digest and recent test
evidence before installing the immutable extension version. These tests prove the
recorded steps executed, not arbitrary natural-language instruction semantics.

## Verification

- Real managed Chromium tests cover isolated accounts, redirects before origin
  contact, revoked/stale authority, private races, one-time sensitive actions,
  parameterized recording replay and current-draft activation gates.
- Real Claude Code initialization connects the in-process MCP without a model
  query. MCP protocol tests drive real Chromium and resume an exact approval.
- Codex stdio fixture and official generated local protocol schema verify the
  approval port. ACP tests execute actual brokered file and process effects.
- Opt-in `TERMX_TEST_NATIVE_WINDOW=1` captures only the temporary owned AppKit
  application created by its macOS test; macOS capture passed locally. Windows
  and X11 capture runtime remain cross-platform CI/host validation requirements.
- `tests/workspace_browser_e2e.py` uses the actual built React workspace and
  isolated managed-auth host. Final proof reports live frames, handoff, approval
  resume and private human view/context exclusion, zero UI errors and zero paid
  provider queries. No production reviewer is represented as qualified without
  its explicit evaluation/configuration.
