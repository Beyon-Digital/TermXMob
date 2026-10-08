# TermX desktop workspace implementation

The unchanged [approved v0.3 HTML](plan-v0.3.html) is authoritative. The user
requested the entire plan, backend first, then the independent desktop/web UI,
and authorized parallel agents without step-by-step approval prompts.

## Current delivery

The resumed source candidate now passes the full frontend suite:241 tests in64 files, plus production TypeScript/Vite build. Final integration includes canonical groups/organizations and audit retention, configurable non-widening device lifetimes, atomic shared tree budgets (planning and concurrent worker time included), safe generic remembered approvals, selected-message/file/summary forks, functional manager search and host-observed browser operation previews. Exact source/asset boundaries are in [final-contract-source-candidate.json](verification/final-contract-source-candidate.json); coherent CI and installed/live qualification remain separate.

Actual Access session-policy proof passes12theme/zoom states, scoped grants/revocation, current-revision conflicts and exact lifetime saves. The previous CJW group/audit proof is preserved. Editable Figma now contains95 source-mapped advanced states and234 verified navigation links; the original11screens/66components remain preserved. [Full requirement traceability](planning-requirement-index.json) includes20tables/171rows and94prose blocks.

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
| PAR-06 delivery | Real Git worktrees/hunk/stage/commit/push, exact durable publication and typed GitHub PR/checks; actual draft PR28 created | None in GIT-01; actual rendered worktree/terminal/conflict/cleanup/PR checks proof passed |
| PAR-07 browser/Computer | Real host Chromium tabs/profiles, scoped task handoff/takeover/private login, annotations/transfers/crash recovery; exact-window and tab recording/redaction/replay | None in CAP-01/BROW-01–05/A11Y-01 for the named qualified modes; actual owned Windows PrintWindow now passes; installed platform permission/input smoke remains open |
| PAR-08 managers | Real registry/custom adapters/private sources, install/update/rollback/permission diff/in-use leases; model/agent/MCP/memory/hooks/automation/access managers | None in EXT-01/02; broader keyboard/contrast/zoom proof passed34loaded states |
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

Subsequent source CI on806e6c7 is green: Linux787passed/129skipped, macOS818passed/98skipped, workspace57tests and production build. See verification/ci-806e6c7.json. Artifact qualification is separate; current macOS ARM complete-runtime signing and frozen smoke passed, while Linux browser crash qualification exposed a one-second fixture readiness race. The test now waits for the actual crash event with a bounded deadline while retaining revocation/new-tab assertions.

Reviewer account rotation now invalidates safety qualification, cached permits and native queued approvals before effects, including credentials changed after a response. Private credential fingerprints never enter reports. New immutable evaluation exports record administrator pricing provenance and actual provider-reported token estimates; missing cost remains explicitly incomplete. These no-charge protocol regressions do not qualify a live smaller model.

Final rendered Git proof passed against index-C5vlYW_B with zero UI errors, paid queries or external mutations. It verifies two UI-created worktrees, exact real PTY success/failure output, hunk staging, commits and local-bare pushes, dirty cleanup/divergent pull refusals, clean worktree cleanup selection recovery, live run/draft/buffer/PTY continuity and concurrent independent dirty-buffer reload. Actual PR28/checks were inspected read-only. Browser close/reopen/re-dock recovery and installed native handoff remain separate docking work. Current frontend suite:62tests in22files passed.

Actual artifact UI proof creates and edits five kinds, saves version2, verifies unchanged version1, and inspects real DOCX/XLSX/PPTX XML plus SVG/TXT exports. Four loaded theme/zoom states pass axe with no overflow/UI errors. Explicit artifact selector labels fix accessible names containing option text. Image/audio live entitlement is still pending; no provider request was sent.

Source CI58e0732 passed Linux808tests/132skips, macOS839tests/101skips and workspace62tests/build. The fast Windows contracts exposed two real ConPTY launch API failures and two worker fixture stalls; these remain unqualified pending the restricted-parent correction and bounded startup diagnostics. Artifact806 Windows backend passed799tests/115skips with only those two worker stalls failing. A fixture-generated termination exit code is not evidence that the worker exited before the timeout.

The full-HTML audit additionally found System appearance and browser targeting/history/diagnostics shortcuts missing from the original summary. System preference now resolves live device changes, survives reload and has an explicit three-choice accessible picker. The actual media/System proof passes live dark/light changes, reload persistence and zero appearance-dialog axe violations. Browser annotated selection/frame provenance, visible history/diagnostics and protected remote shortcuts are being completed independently of the already verified browser authority backend.

Source CI e102f3f passed Linux810tests/132skips, macOS841tests/101skips and workspace62tests/build. Separate Windows e102 qualification passed167tests/1skip with3remaining failures: two restricted ConPTY output checks and one worker reaching a noninteractive shell after its first tool effect. Native private-storage DACL tests passed. Checkpoint2a57243 gives noninteractive shell and mutating Git explicit EOF stdin; three real subprocess regressions keep the caller control pipe open and verify normal exit. Actual Windows qualification follows CI, rather than inferring the platform result from these local tests.

AUTH-02 storage/session contract is now verified: native Windows bootstrap storage passes all three actual protected-DACL/junction/foreign-owner tests; backend private-file, SQLite-journal and real DPAPI contracts pass. The overall Windows job still fails unrelated console/worker checks. Installed native unlock/restart/window recovery remains separate UX/DOCK/VIEW work. See verification/native-private-storage.json.

Source checkpoint2a57243 passes Linux812tests/132skips, macOS843tests/101skips and workspace62tests/build. Actual Windows passes171tests/1skip plus3native storage tests; all worker/control-stdin regressions pass. Only two restricted ConPTY no-output tests remain failing. A restricted-process console broker is in progress; no host execution fallback is claimed. See verification/ci-2a57243.json.

Actual browser window lifecycle proof now passes toolbar/per-panel detach, close/reopen with independent dirty buffers/text/images, focusing an existing named window, conflicting main draft refusal, durable acknowledged re-dock, image-only closed-slot recovery and unchanged active task/PTY/disk. Root inspected the loaded recovered Chat image and audited visible dirty-tab assertions in the fixture. Installed native lifecycle qualification remains open. See verification/browser-window-lifecycle.json.

Artifact806e6c7 completed with an ARM DMG/updater workflow artifact and accepted/stapled notarization. Intel also completed frozen smoke, signing and accepted/stapled notarization, then failed DMG detachment with an explicit DiskArbitration timeout; no Intel installer is claimed. The documented official Tauri action retry input now permits one bounded macOS rebuild, retaining signing/bundling failure gates. Linux crash-event and Windows worker stdin fixes passed later source tests; refreshed full installer qualification is still required. No release/updater was published. See verification/artifact-806e6c7.json.

Full HTML section10 command requirements are now implemented: searchable workspace commands/settings/views/keybindings, default Mod+Shift+P palette and scoped filename-only Quick file open via remappable Mod+P/visible control. Actual UI proof selects only the active enrolled checkout, preserves another dirty editor buffer and unchanged disk, and passes named combobox/selected-option/axe checks at100/200%. Server revalidates live scope/checkout after searching and excludes outside symlinks. Root fullfrontend suite passed79tests/29files before the final quick-file ARIA follow-up, whose3targeted tests and production build passed. See verification/quick-file-workspace.json.

Source checkpoint795f78c passes Linux843tests/136skips, macOS874tests/105skips and workspace62tests/build. Actual owned Windows PrintWindow capture passes exact dimensions/pixels and foreign-owner refusal. Restricted console diagnostics exposed inherited daemon protocol stdio; c6ce473 explicitly nulls ConPTY standard handles and awaits actual Windows output qualification. No unrestricted host fallback is enabled. See verification/ci-795f78c.json and windows-owned-capture.json.

Actual Windows checkpoint7d9a4b6 passes208contracts/1skip plus3native-private-storage tests. Restricted ConPTY now passes real console input/output,120x45resize, Ctrl+C with process survival, inside/outside write boundaries, filtered host environment and descendant cleanup before write-grant release. There is no unrestricted production fallback. Linux843/macOS874/backend and workspace62tests/build also pass. New preset/voice/provider/native-microphone/browser changes await combined-source qualification. See verification/ci-7d9a4b6.json.


### Historical coordinated runtime checkpoints

Source `3edf12c` adds immutable admitted custom-agent presets, explicit provider media declarations, synthetic voice capture/transcription/reviewed-send/speech, window recovery, named System appearance and searchable commands, browser annotation evidence/diagnostics/tab lifecycle, and private Computer input epochs. Frontend107tests/build pass. Frozen `index-t8fN_0qQ.js` passes eight Chat/Workbench states, four voice states,22Browser states/215keyboard checks and22Computer states/62keyboard checks; inspected200%footer controls fit, including expired-private recovery. No CSS injection, paid query or physical microphone evidence is claimed.

Full source CI37627728055 failed18tests on each backend platform because four private BrowserService test fixtures were not closed. Checkpoint `57996e7` closes those services in finally blocks, preserving production observation barriers; the affected29-test sequence passes with1optionalChromium skip. Its full CI37629702706 is pending. The restricted local full run was interrupted after localhost binding was denied and is not qualification; the isolated localhost-enabled run is diagnostic until complete.

The full-scope audit found further required corrections: independent Computer docking identity through Focus/detach/simultaneous canvases and a scoped remembered-rule edit flow. These are being implemented and will receive focused rendered qualification. Original38-item acceptance remains32verified/6partial while installed native and real entitled provider/reviewer gates are open. The current artifact-only run37627744804 targets3edf and cannot qualify later source changes.

CI57996e7 subsequently passed: Linux901tests/142skips, macOS933tests/110skips, workspace107tests/34files and production build. Exact clean-runner results are in verification/ci-57996e7.json; later Computer docking/rule edits remain separately pending.


Current full-HTML corrections now have separate rendered evidence: independent Computer docking; inspect/edit/revoke remembered rules; exact explicit browser paste; content/provenance/retention memory editing with revision conflict recovery; and toolset/budget preset editing for future runs. The preset proof was repeated against immutable `index-fCsPFH47.js` after checkbox alignment, with zero axe/overflow/page errors in four theme/zoom states. Root inspected the light200% result. The started frontend suite passed140tests/44files; subsequent Lock voice cancellation adds four meaningful races/playback checks and the15-test voice suite passes. Layout presets/artifact-window continuity and new Lock/unlock/Stop-host integration are still being qualified rather than inferred from that older suite.

Native mobile compatibility is restored in the companion draft https://github.com/Beyon-Digital/termx-app/pull/26 at `ee0a90f8303e41478e2233cc8bf6becfbf5e5827`. Its clean CI37638221463 passes typecheck,68tests/9files and Expo export. Refresh credentials are native SecureStore-only, access JWTs stay in memory, socket URLs use exact one-use tickets, and authorized media resolves through private temporary files. Existing unmanaged-mobile artifacts are preserved; the desktop/external-browser surface remains the independent workspace. Actual installed phone qualification and live entitled model runs remain explicit limits. See verification/companion-mobile-ci.json and managed-mobile-pairing.json.
