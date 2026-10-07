# Workspace runtime support and release gates

This is an implementation/verification matrix, not a declaration that all v0.3
acceptance gates have passed. The approved HTML and acceptance ledger govern
completion. Updated 7 October 2026.

## Presentation and identity

| Surface | Concrete path | Verified | Remaining |
| --- | --- | --- | --- |
| Browser workspace | Shared React DOM contract3, same-origin cookie+CSRF, host-side projects/sessions/tabs | Actual localhost production UI, content-ready large fixture, viewport/keyboard/axe proof | Remote HTTPS deployment and broader combined flows |
| Native desktop | Tauri loads same workspace, exact origin/window/API route bridge; keyring-held refresh; single-flight rotation; binary transfer | Rust source/test compilation; actual Windows OS keyring roundtrip and native bridge tests; stable per-panel/session monitor geometry recovery | Installed GUI password/SSO, actual detach/multi-monitor and signed installer smoke |
| Expo mobile | Existing compatible host API, persisted legacy-token migration deadline | Backend migration contracts | Mobile managed-session presentation rollout before migration expires |
| Local password | Scrypt, managed short JWT/hashed rotating refresh, limiter/revocation | Real application/service tests | Deployment smoke |
| OIDC | Discovery, code+PKCE/state/nonce, RS256 issuer/audience/subject mapping | Real localhost TLS IdP including native exact-loopback callback and replay refusal | Enterprise IdP/device installation smoke |
| Custom identity | Host-admin configured signed assertion reference adapter, canonical session/authorization | Concrete adapter fixtures and staged activation tests | Each integrator qualifies its own adapter |

## Agent and action enforcement

| Adapter/workflow | Enforcement currently implemented | Validation/limit |
| --- | --- | --- |
| Internal local | Existing AgentManager loop; typed ToolRegistry and host-derived reviewed envelopes; exact one-use permits; isolated child paths | Actual tools and adversarial fixtures; real30-action Chromium proof:25host-rule and5fixture reviewer decisions,30audits/0human prompts; real reviewer unqualified |
| Claude browser session | SDK in-process MCP, four host browser tools, no built-in tools/settings/skills or extra MCP | Real Claude CLI initialization/MCP connected and real browser tools tested without paid provider query |
| Claude coding session | SDK pre-tool and post-success/failure hooks, typed host proposals, one-time external permit lifecycle | Pre/post-tool lifecycle and regression tests pass; native sandbox tools require the documented adapter contract |
| Native Codex/ACP | Codex stdio command/file/capability approval callbacks and ACP host filesystem/terminal effects use the host broker; strict native browser/cloud modes are refused | Actual stdio/file/terminal callbacks tested. Native sandbox fastpaths remain native policy; no universal interception claim |
| Internal cloud | Existing loop inside dedicated network-none container, provider/review RPC over exec stdio; explicit session/schedule location and named credential reference | Existing-loop worker, immutable image probe and session/schedule target checks implemented; actual network-none Docker reviewed file/shell, artifact and cancellation proof passed; remote VM proof open |

Remembered safe rules and qualified-model decisions are different audited paths.
The smaller reviewer is **not yet qualified** against a real configured model.
Unavailable/malformed/stale review pauses eligible actions; sensitive/unknown
transactions retain exact human decisions. Declared browser authority cannot
be expanded through shell, arbitrary network, private observations or executor
summaries.

## Development and creation adapters

| Capability | Actual adapter | Verified path | Remaining |
| --- | --- | --- | --- |
| Python debug | debugpy stdio DAP | Real launch/loopback attach/breakpoint/step/stack/variables; conversation checkout and reconnect | Frozen adapter plus separately installed host Python interpreter smoke |
| JS/TS debug | Microsoft js-debug1.140.0 standalone loopback DAP; pinned artifact SHA | Real JS and TS source-map breakpoint/step/stack/variables; actual frozen Linux/Windows DAP initialize | Full cross-platform installed breakpoint qualification |
| Python LSP | basedpyright | Real navigation/hover/completion | Frozen Linux/Windows protocol initialize/shutdown passed; full installed navigation pending |
| TypeScript LSP | typescript-language-server4.3.3/TypeScript5.9.3 | Real project navigation and formatting | Frozen Linux/Windows protocol initialize/shutdown passed; full installed navigation pending |
| Git delivery | Real Git, exact durable action, local isolated worktrees | Hunk/stage/commit/push, divergent FF-only pull, dirty cleanup, reviewed index/content/target and unknown outcome non-replay tests | Selected-worktree rendered workflow and cross-platform packaged checks |
| GitHub | Existing host gh authorization, typed API payloads | Actual reviewed push/draft PR #28 creation and passing/failed check inspection | Full rendered review workflow |
| Exact-window capture | macOS CoreGraphics + exact screencapture ID; Windows PrintWindow; X11 exact window ID | Actual owned macOS AppKit window captured; private/redaction/parameterized test-before-activate service tests pass | Windows/X11 installed capture and platform permission smoke |
| Host preview | Separate exact-origin ephemeral Chromium worker | Actual host project server, render/input, isolation and revocation cleanup | Installed worker smoke |
| Office/chart artifacts | python-docx/openpyxl/python-pptx/escaped SVG, immutable versions | Actual DOCX/XLSX/PPTX/CSV/SVG exports | Combined live voice-to-artifact flow needs an entitled API account |
| Input conversion | PIL, pypdf, python-docx, bounded ffmpeg sampling | Actual image/PDF/DOCX/video conversions and expansion limits | Live audio transcription account |
| Image/audio generation | Explicit configured OpenAI-compatible API account/model | Real HTTP protocol serialization and safe failure/dedup fixtures | At least one real entitled image/edit/transcribe/speech adapter per capability |
| Runners | Existing owner Docker image, frozen imageID, local context or SSH Docker endpoint | Actual nonroot container/upload/scoped secret/results/budget/teardown | Actual cloud loop, scoped artifact and cancellation passed; remote VM/SSH and installed lease proof open |

No automatic image pull, account/billing fallback, host cookie import or local
Rust delivery build occurs. Dedicated runner filesystem/network/secret choices
are explicit; reconnect cannot migrate a local run into the cloud.

## CI and deployment

The desktop workflow prepares the dedicated UI, pinned Chromium, language/debug
runtimes, media/office extras, frozen Python sidecar and platform installer. The
frozen runtime probe is a required packaging check. Artifact-only dispatch leaves
release_tag empty; it does not publish an updater or a release.

A configured signing identity is not evidence of a valid signature. CI/device
proof must cover installer signatures, native keyring/SSO, platform capture,
monitor restore and packaged debug/browser/media paths. These gates remain open.
