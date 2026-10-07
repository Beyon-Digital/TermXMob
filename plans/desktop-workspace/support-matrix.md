# Workspace runtime support and release gates

This is an implementation/verification matrix, not a declaration that all v0.3
acceptance gates have passed. The approved HTML and acceptance ledger govern
completion. Updated 7 October 2026.

## Presentation and identity

| Surface | Concrete path | Verified | Remaining |
| --- | --- | --- | --- |
| Browser workspace | Shared React DOM contract3, same-origin cookie+CSRF, host-side projects/sessions/tabs | Actual localhost production UI, content-ready large fixture, viewport/keyboard/axe proof | Remote HTTPS deployment and broader combined flows |
| Native desktop | Tauri loads same workspace, exact origin/window/API route bridge; keyring-held refresh; single-flight rotation; binary transfer | Rust source/test compilation; actual Linux/Windows OS keyring roundtrip and native bridge tests; stable per-panel/session monitor geometry recovery | Installed GUI password/SSO, actual detach/multi-monitor and signed installer smoke |
| Expo mobile | Existing compatible host API, persisted legacy-token migration deadline | Backend migration contracts | Mobile managed-session presentation rollout before migration expires |
| Local password | Scrypt, host-pinned HS256 issuer/audience, key rotation, managed short JWT/hashed rotating refresh, configurable non-widening device idle/absolute limits, limiter/revocation | Real application/service tests | Deployment smoke |
| OIDC | Discovery, code+PKCE/state/nonce, RS256 issuer/audience/subject mapping | Real localhost TLS IdP including native exact-loopback callback and replay refusal | Enterprise IdP/device installation smoke |
| Custom identity | Host-admin configured signed assertion reference adapter, canonical session/authorization | Concrete adapter fixtures and staged activation tests | Each integrator qualifies its own adapter |

## Agent and action enforcement

| Adapter/workflow | Enforcement currently implemented | Validation/limit |
| --- | --- | --- |
| Internal local | Existing AgentManager loop; typed ToolRegistry and host-derived reviewed envelopes; exact one-use permits; isolated child paths | Actual tools and adversarial fixtures; real30-action Chromium proof:25host-rule and5fixture reviewer decisions,30audits/0human prompts; real reviewer unqualified |
| Managed coding consent | Host-advertised task/conversation/device/project/preset scopes; owner/current-policy matching, immutable exact operation/sandbox and narrow CAS edit/revoke; matching denies and final authority validation remain enforced | Actual AQa production proof: two exact typed effects after one human decision, separate coding-policy audit, inspect/edit/revoke and four theme/zoom states. Structured credential/unknown/sensitive actions stay exact once-only with no model/remembered bypass; native requests expose only declared options. [Evidence](verification/coding-consent-rendered-AQa.md) |
| Claude browser session | SDK in-process MCP, six host browser tools (tabs, observe, action, open/close and wait for handoff), no built-in tools/settings/skills or extra MCP | Real Claude CLI initialization/MCP connected and real browser tools tested without paid provider query |
| Claude coding session | SDK pre-tool and post-success/failure hooks, typed host proposals, one-time external permit lifecycle | Pre/post-tool lifecycle and regression tests pass; native sandbox tools require the documented adapter contract |
| Native Codex/ACP | Codex stdio command/file/capability approval callbacks and ACP host filesystem/terminal effects use the host broker; strict native browser/cloud modes are refused | Actual stdio/file/terminal callbacks tested. Native sandbox fastpaths remain native policy; no universal interception claim |
| TermX MCP selections | Internal per-effect broker validates project, current principal/SID, connection/catalog snapshots and approved tools; direct project-scoped native MCP is refused | Codex per-session MCP selection is unsupported and refused before native startup; existing Codex host configuration remains native-owned. Global trusted Claude/ACP native bindings retain their stated adapter boundary |
| Internal cloud | Existing loop inside dedicated network-none container, provider/review RPC over exec stdio; explicit session/schedule location and named credential reference | Existing-loop worker, immutable image probe and session/schedule target checks implemented; actual network-none Docker reviewed file/shell, artifact and cancellation proof passed; remote VM proof open |

Remembered safe rules and qualified-model decisions are different audited paths.
The smaller reviewer is **not yet qualified** against a real configured model. Private account revision binding invalidates model qualification and cached/native permits on credential changes. Immutable evaluation exports include explicit administrator pricing and provider-reported input/cached/output token estimates; absent rates or incomplete usage remain incomplete cost evidence.
Unavailable/malformed/stale review pauses eligible actions. Eligible browser
transactions require exact redacted host-observed evidence; unknown, incomplete,
invisible or sensitive browser proposals and recognized CAPTCHA/MFA require
manual takeover. Generic unknown coding effects retain exact once-only human
decisions. Scoped data requests remain refused after automatic revocation and
popup/retry attempts until explicit human recovery. [Fixture proof and recognition limits](verification/browser-transaction-boundary.md)
remain distinct from production renderer/native/live-model qualification. Declared browser authority cannot
be expanded through shell, arbitrary network, private observations or executor
summaries.

## Development and creation adapters

| Capability | Actual adapter | Verified path | Remaining |
| --- | --- | --- | --- |
| Python debug | debugpy stdio DAP | Real launch/loopback attach/breakpoint/step/stack/variables; conversation checkout and reconnect | Frozen adapter plus separately installed host Python interpreter smoke |
| JS/TS debug | Microsoft js-debug1.140.0 standalone loopback DAP; pinned artifact SHA | Real JS and TS source-map breakpoint/step/stack/variables; actual frozen Linux/Windows DAP initialize | Full cross-platform installed breakpoint qualification |
| Python LSP | basedpyright | Real navigation/hover/completion | Frozen Linux/Windows protocol initialize/shutdown passed; full installed navigation pending |
| TypeScript LSP | typescript-language-server4.3.3/TypeScript5.9.3 | Real project navigation and formatting | Frozen Linux/Windows protocol initialize/shutdown passed; full installed navigation pending |
| Git delivery | Real Git, exact durable action, local isolated worktrees | Hunk/stage/commit/push, divergent FF-only pull, dirty cleanup, reviewed index/content/target and unknown outcome non-replay tests | Actual rendered selected-worktree/PTY/hunk/push/conflict/cleanup journey passed; cross-platform packaged checks pending |
| GitHub | Existing host gh authorization, typed API payloads | Actual reviewed push/draft PR #28 creation and passing/failed check inspection | Actual read-only rendered PR28/current checks passed; full review publication uses explicit consent |
| Exact-window capture | macOS CoreGraphics + exact screencapture ID; Windows PrintWindow; X11 exact window ID | Actual owned macOS AppKit, CI X11 and Windows PrintWindow windows captured; private/redaction/parameterized test-before-activate service tests pass | Installed platform permission/input smoke remains open; Windows exact owned360x240 capture and foreign-owner refusal passed on795f78c |
| Host preview | Separate exact-origin ephemeral Chromium worker | Actual host project server, render/input, isolation and revocation cleanup | Installed worker smoke |
| Office/chart artifacts | python-docx/openpyxl/python-pptx/escaped SVG, immutable versions | Actual five-kind UI edit/version/inspect/export plus DOCX/XLSX/PPTX/CSV/SVG adapters; four loaded theme/zoom states pass axe | Combined live voice-to-artifact flow needs an entitled API account |
| Input conversion | PIL, pypdf, python-docx, bounded ffmpeg sampling | Actual image/PDF/DOCX/video conversions and expansion limits | Live audio transcription account |
| Image/audio generation | Explicit configured OpenAI or compatible API account/model | Real HTTP protocol serialization and safe failure/dedup fixtures | At least one real entitled image/edit/transcribe/speech adapter per capability |
| Runners | Existing owner Docker image, frozen imageID, local context or SSH Docker endpoint | Actual nonroot container/upload/scoped secret/results/budget/teardown | Actual cloud loop, scoped artifact and cancellation passed; remote VM/SSH and installed lease proof open |

No automatic image pull, account/billing fallback, host cookie import or local
Rust delivery build occurs. Dedicated runner filesystem/network/secret choices
are explicit; reconnect cannot migrate a local run into the cloud.

## CI and deployment

The desktop workflow prepares the dedicated UI, pinned Chromium, language/debug
runtimes, media/office extras, frozen Python sidecar and platform installer. The
frozen runtime probe is a required packaging check. Artifact-only dispatch leaves
release_tag empty; it does not publish an updater or a release.

Source checkpoint `02f987be049075298ebd17da5a0e9d035b22d39a` passes245 frontend,
1035 Linux and1067 macOS tests, plus the earlier focused Windows contracts and
native compile checks. The exact-source artifact run37677911211 found eight
additional failures in the full Windows suite (1048 passed,126 skipped); Windows
sidecar, installers and installed-MSI checks did not run. The [failure record](verification/windows-installer-02f987b-failures.json)
preserves that boundary. Subsequent Windows byte-revision, skill-root and fixture
repairs require their own exact-source Windows and installer qualification.

[Rollout notes](rollout-notes.md) document private stopped-host snapshots,
managed-auth migration and owner recovery prerequisites. Automatic schema
downgrade and actual snapshot restoration remain unqualified.

A configured signing identity is not evidence of a valid signature. CI/device
proof must cover installer signatures, native keyring/SSO, platform capture,
monitor restore and packaged debug/browser/media paths. These gates remain open.
