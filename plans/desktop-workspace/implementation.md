# TermX desktop workspace implementation

The unchanged [approved v0.3 HTML](plan-v0.3.html) is authoritative. The user
requested the entire plan, backend first, then the independent desktop/web UI,
and authorized parallel agents without step-by-step approval prompts.

## Current delivery

All ten feature families have concrete backend/frontend work. Delivery remains
in progress: partial implementation and adapter fixtures are not full acceptance.
The exact 38 requirements, current evidence and outstanding gates are tracked in
[acceptance.json](acceptance.json).

| Family | Implemented | Remaining acceptance |
| --- | --- | --- |
| PAR-01 access | Canonical principals, local password/OIDC/custom adapters, managed JWT/refresh, roles/project/resource guards, adapter staged activation, native keyring bridge | Cross-platform installed keyring/SSO and rollout smoke |
| PAR-02 chat | Independent React DOM chat, canonical durable sessions, session-only engine/model selection, streaming, context, linked forks, latest-turn pagination | Final rendered continuation/performance and live provider journeys |
| PAR-03 memory | Provenance, exclusions, scoped versions, retention/export, bounded lifecycle hooks | Final combined isolation/runtime proof |
| PAR-04 execution | Goals, delegated schedules, timezone/missed/overlap rules, leases/budgets, isolated child worktrees/snapshots, supervision | Actual delegated cloud schedule and full aggregate budget journey |
| PAR-05 coding | CodeMirror buffers, PTY/xterm, diagnostics/navigation/completion/formatting, real debugpy and Microsoft js-debug DAP | Installed runtime smoke |
| PAR-06 delivery | Worktrees, hunk/stage/commit/push, exact one-time publication, typed GitHub PR/review/checks port | Live PR/checks/conflict and cleanup workflow |
| PAR-07 browser/Computer | Host Chromium tabs/profiles, rendered canvas, scoped handoff/takeover/private mode, annotations/transfers, browser skill drafts, Computer watch/control | Rendered explicit resume, cross-platform capture and signed worker packaging |
| PAR-08 managers | Registry/manifest adapters, private sources, install/update/rollback, permission diffs, active-use leases, configured model/agent/MCP managers | Combined extension/capture qualification journey |
| PAR-09 desktop | Arbitrary docking/tab groups, keyboard shortcuts, persistent layouts, detach, monitor-aware native restore, themes | Extended loaded-editor a11y proof, real platform windows/monitors |
| PAR-10 creation/runners | Versioned document/table/chart/deck/code exports, image/audio protocol adapters, bounded media conversion, real dedicated containers/secret injection/logs/results/teardown | Real entitled image/audio/reviewer account, remote VM proof and packaged runtime qualification |

The native desktop and browser use the same contract3 independent workspace
bundle. Expo remains the mobile client. An absent/incompatible workspace bundle
returns an actionable error; it is never replaced with the Expo desktop UI.

## Important boundaries

- A role/JWT does not isolate unrestricted programs executing as the same OS
  user. The documented local boundary is a trusted shared machine; restricted
  execution requires isolated containers/VMs and scoped credentials.
- Built-in browser grants authorize exact tabs/origins/tasks; they cannot grant
  arbitrary shell/network or transaction consent. Private observation is
  suppressed before and after awaited operations, and takeover invalidates
  queued permits.
- Auto Review uses host-derived action envelopes, live authority, remembered
  rules, and qualified model versions. Rules and model decisions are audited
  distinctly. No configured smaller model has yet completed live qualification.
- Internal tools and the dedicated Claude browser MCP workflow have enforceable
  paths. Native Codex/ACP restricted browser/cloud workflows remain explicitly
  unavailable until interception is proved. Unsupported adapter capability is
  an outstanding acceptance gate, not delivered parity.
- Provider accounts/models and charge acknowledgement are explicit. No silent
  subscription/API fallback or paid validation has occurred. Uncertain media
  outcomes survive restart and cannot be replayed automatically.
- Runner location, image, network, workspace copy and secret references are
  explicit. Dedicated nonroot containers use read-only roots, bounded tmpfs,
  independent leases, budgets, and no host mounts/Docker socket. The actual existing AgentManager loop also runs inside a network-none
  container over provider/review RPC; reviewed file/shell effects, exported
  artifacts and cancellation stopping the container pass. No provider key
  enters the worker. Remote VM/SSH deployment remains unverified.

## Verification on 7 October 2026

- Full backend run at an earlier integration checkpoint: **727 passed,73 skipped,
  4 failed**. The approval timing failure passed isolated and in the subsequent
  133-test affected run; the empty-Claude-catalogue failure was fixed and passed.
  The macOS developer shim and Bash ready-marker failures also reproduce on the
  unchanged baseline. Both baseline failures were diagnosed and fixed: the toolchain execution
  grant is restricted to the already-readable /Library/Developer tree; the PTY
  foreground test runs without user startup hooks and always cleans up.
  All 34 real PTY/macOS sandbox regressions now pass.
- Subsequent affected agent/catalogue/transport/Relay run: **133 passed**.
- Actual localhost TLS IdP (discovery, authorization, PKCE, nonce, token/JWKS,
  exact HTTPS/native redirect and replay denial) uses real HTTP/TLS, not a mock
  transport. Native Rust source checks pass; no Rust delivery binary was built
  locally.
- Real Python/JavaScript breakpoint/step/stack/variables and real Python/
  TypeScript language-server navigation pass. TypeScript source-map entrypoint barriers now pass real breakpoint, step,
  stack and variable inspection (4debug tests pass).
- Media tests: **6 passed**, including real Office exports, PDF/document/video/
  image conversion, archive expansion limits, scoped operation metadata,
  restart reconciliation, provider protocol and billing dedup.
- Dedicated runner tests pass against an existing real Docker image: workspace
  upload, nonroot identity, secret redaction, result retrieval, budget stop and
  teardown. The real network-none AI worker also passes reviewed file/shell, artifact
  retrieval and canonical cancellation tests. Live remote SSH/VM remains open.
- The localhost production UI exercised500 conversations and10000 retained
  turns:4413ms authenticated content-ready load,277ms layout move,
  12.7MiB used JS heap, no duplicate turn from a second window. Viewports1280,
  1440,1920,2560,768 and390 had no page overflow. Visual review found and corrected empty SVG downloads and narrow-sidebar
  state. The corrected content-ready1440/390renders were inspected.
- Independent axe checks at100%/200%, both themes, reduced motion and keyboard
  found missing separator values and scroll-region focus. Initial fixes passed axe 4.10.3 in both themes at 100%/200%. Extended
  Workbench/editor coverage exposed a slow draft-remount race; source fixes
  and actual unmount/revision regression tests pass, final rendered rerun pending.

See the test sources and acceptance ledger for precise scope. Test fixtures and
protocol adapters do not establish real provider entitlement, a qualified
reviewer, remote infrastructure, signed packaging, or cross-platform behavior.
No release, deployment or billing fallback is implied by these checks.

The second full-suite checkpoint completed **756 passed, 84 skipped, 5 failed**
in 641.66 seconds. It loaded earlier code before ongoing agent updates; three
native broker failures used the older BrowserService constructor, and two were
the baseline issues described above. Current focused native and baseline reruns
pass. A clean frozen-source full-suite/CI run is still required.

Git confirmations now bind staged index, branch, remote configuration and
affected working content. Uncertain publication responses retain consumed IDs.
Media provider streams and PDF expansion are bounded before oversized output
allocation; provider/runner intent IDs persist across reloads without storing
input content or credentials. Latest root Git/media verification: 15 passed.

The clean current-source full backend run subsequently passed: **767 passed,
87 skipped**, 287.54 seconds. Live draft PR #28 and exact reviewed push/PR-create
completed through the production GitHub delivery port. The frontend GitHub check
passed. Linux/macOS CI identified the pytest-command fixture import boundary;
that import is corrected and all three actual TLS OIDC cases pass under the
exact pytest entrypoint. Cross-platform packaging CI is rerunning at the fix.

Latest integration adds conversation-bound worktree selection across file reads,
saves/search, terminal creation, language-server roots, agent execution and debug
sessions. Real Git regression proves scoped worktree edits leave the main checkout
unchanged, denies symlink escape and revoked writes, and binds unattended grants
to the exact checkout identity. Removed worktrees are rejected while the session
can recover by selecting a live checkout.

The installed debugger/Relay/transport regression passed **11 tests**: Python
launch and direct loopback attach with breakpoint/variable inspection, JS/TS
source-map stepping, exact conversation checkout and reconnect discovery,
revoked projection rejection, and viewer input denial. Stopping Python attach
detaches without terminating the user's process. Viewer-only sockets no longer
release keys held by a controller during disconnect cleanup.

Combined-load stack sampling found synchronous session-list projection and
per-frame identity SQLite checks blocking the ASGI loop. Both now run on IO
workers, retaining per-message live authority and final policy checks. The
combined rendered activity proof is rerunning; previous multi-second stalls are
not treated as meeting the performance gate.

CI now exercises synthetic OS credential-store write/read/delete on all three
platforms. Frozen runtime verification executes bundled Node, FFmpeg, all five
language servers, debugpy and Chromium rather than only locating files. Actual
development runtime smoke passed; frozen execution and signing remain CI gates.
