# TermX desktop workspace implementation

The unchanged [approved v0.3 HTML](plan-v0.3.html) is authoritative. The user
requested the entire plan, backend first, then the independent desktop/web UI,
and authorized parallel agents without step-by-step approval prompts.

## Current delivery

All ten feature families have concrete backend/frontend work. Delivery remains
in progress: partial implementation and adapter fixtures are not full acceptance.
The exact 38 requirements, current evidence and outstanding gates are tracked in
[acceptance.json](acceptance.json).

| Family | Implemented and exercised | Remaining plan acceptance |
| --- | --- | --- |
| PAR-01 access | Canonical principals, local password/real TLS OIDC/custom adapter, JWT/refresh, roles/project/resource guards, native keyring bridge; live revoke/legacy tests and real Linux/Windows OS credential store | Installed native first-boot/restart/revoke integration |
| PAR-02 chat | Independent React DOM chat, durable session-only engine/model selection, streaming, context, linked forks, receipt recovery; simultaneous engine configuration isolation | Native active-run/window continuation integration |
| PAR-03 memory | Provenance, exclusions, scoped versions, retention/export and bounded lifecycle hooks | None in MEM-01; integrator adapters retain their own qualification |
| PAR-04 execution | Durable goals/schedules, timezone/missed/overlap rules, delegated target/budget/leases, isolated child worktrees/snapshots, supervision; actual network-none container worker | None in AUTO-01/AGENT-01/CLOUD-01 for the named qualified container adapter |
| PAR-05 coding | CodeMirror buffers, restricted PTY/xterm, diagnostics/navigation/completion/formatting; actual Python/JS/TS breakpoint/step/inspection and language navigation | Native installed coding/runtime integration; DEV-01 behavior verified |
| PAR-06 delivery | Real Git worktrees/hunk/stage/commit/push, exact durable publication and typed GitHub PR/checks; actual draft PR28 created | Final rendered two-worktree/terminal/conflict/cleanup journey |
| PAR-07 browser/Computer | Real host Chromium tabs/profiles, scoped task handoff/takeover/private login, annotations/transfers/crash recovery; exact-window and tab recording/redaction/replay | Globally persistent recording indicator and final rendered proof; Windows exact-window capture remains a platform limit |
| PAR-08 managers | Real registry/custom adapters/private sources, install/update/rollback/permission diff/in-use leases; model/agent/MCP/memory/hooks/automation/access managers | Final broader keyboard/contrast/zoom proof |
| PAR-09 desktop | Arbitrary docking/tab groups/keyboard, persistent layouts, native detach and monitor-aware restoration; acknowledgement-based state-safe re-dock | Installed native detach/re-dock/missing-monitor proof and final accessibility closure |
| PAR-10 creation/runners | Versioned Office/table/chart/deck/code exports, media conversions and explicit image/audio protocols; real dedicated container/secret/logs/results/budget/teardown | Real entitled image/edit/transcription/speech account and frozen smaller-reviewer evaluation with latency/cost |

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
  unavailable until interception is proved. Unsupported adapter capability remains explicit in the support matrix; a
  supported run cannot silently bypass enforcement or change adapters.
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
workers, retaining per-message live authority and final policy checks. The final
combined rendered activity proof passed with two content-ready windows, 500
sessions, 10000 turns, PTY output and actual browser actions. The complete report
is retained in verification/combined-load.json; latency tails remain disclosed.

CI now exercises synthetic OS credential-store write/read/delete on all three
platforms. Frozen runtime verification executes bundled Node, FFmpeg, all five
language servers, debugpy and Chromium rather than only locating files. Actual
development runtime smoke passed; frozen execution and signing remain CI gates.


Latest verification checkpoint

- Combined loaded workload passed: authenticated load2352ms, session filters66–251ms,
  393 PTY frames/82KB, 22 browser frames/325KB, 15 actual controls, heap24.8/17.2MB.
  Browser control maximum1334ms and host loop lag866ms remain visible in the report.
- Eight actual loaded Chat/Workbench accessibility states passed in both themes
  at100%/200%, with visible keyboard focus, reduced motion and no overflow.
  At200%, the editor retains582x176/143 CSS pixels and Split remains reachable.
- CI on273e598 passed actual frozen Linux and Windows execution of Node, FFmpeg,
  five language servers, debugpy, Microsoft JS debugger and Chromium render/PNG.
  Windows also passed native bridge and real OS credential-service roundtrip.
  Later platform fixture failures remain distinct from these passed steps.
- Fixed Linux's missing xmessage dependency, macOS socket cancellation teardown,
  Windows LSP file-URI normalization, and Windows debug fixture cwd/CRLF assumptions.
  Actual root Python debug enrollment-revocation and attach regressions:2passed.
- Browser task control is now live-task scoped: first missing handoff parks the
  exact call, explicit handoff resumes it, and terminal tasks lose control.
  Real Chromium30-action proof distinguishes25host rules from5fixture reviewer
  decisions and records30audits/0human prompts. No real reviewer is qualified.
- Browser context uses labeled, inspectable/removable composer references; exact
  payload and draft persist through layout moves and full reload.

The767pass/87skip backend result is an earlier committed checkpoint. New complete
backend and artifact-only platform reruns must cover these subsequent changes.

Subsequent source checkpoint: complete backend collection passed786tests with96
optional integrations skipped in295.38seconds. The later canonical turn/receipt
restart and follow-up draft fixes passed44targeted tests; browser fault/injection
and native review contracts passed45tests with one optional CLI case skipped.
Active-task rendered browser proof passed all14workflow flags with zero UI errors
and no paid provider queries. New native recovery passed six geometry source tests
and cargo check --tests; actual multi-monitor GUI validation remains open.

Current source integration fixes

- Exact simultaneous-engine regression passes with actual canonical runs. Focused
  live authorization/identity/preview checks:55passed,1optional skip.
- Linux subagent benchmark now assigns parent/child responses to their own prompts
  and explicitly waits for both children. It no longer relies on a timing-sensitive
  shared response queue; six benchmark tests pass.
- macOS restricted interactive terminals now use the same Seatbelt policy, private
  HOME, filtered environment and filesystem roots as agent execution. Actual Bash
  and Zsh interruption/resume and outside-root denial pass.
- macOS external Chromium bypasses PyInstaller's partial Mach-O cache and stages
  the complete runtime with relative framework links. The platform Tauri config
  uses whole-directory copying, preserving these links in the installed app.
  Signing defers designated app/framework executables until nested code is signed.
  Two packaging regressions pass, including actual small C-generated Mach-O helper/
  framework/app strict-signature checks and embedded JIT entitlement inspection.
  These fixtures do not prove the complete Chromium installer; that remains CI work.
- Current Windows CI is not green:15full-suite failures exposed platform fixture
  assumptions, missing timezone data, private-file ACL gaps and two worker timeouts.
  Dedicated adapter verification passed12checks (all debug paths) and failed two
  file-URI comparisons. Fixes and actual platform checks are being integrated.
