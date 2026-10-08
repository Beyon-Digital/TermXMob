# TermX desktop v0.3 — quota handoff

Saved on 2026-10-08 (Asia/Kolkata). The user warned that inference quota was about to expire. This is a durable checkpoint, not a completion claim.

## Scope and authorization

Implement the complete `plan-v0.3.html`, including every Further Parity family, backend first then the dedicated desktop/web frontend. The user approved implementation, subagents, and continuing without routine step approvals. Preserve unrelated changes. Do not build Rust delivery binaries locally; use GitHub CI. Do not merge, publish a release/updater, or silently choose a paid provider. No formal goal was created.

Canonical plan SHA256: `6bd1bdeadbbae81eaeccbf86cd1e42174911255c2194157bbe8a5d6e185ee89a`. Read the local HTML; no external browser is needed to retrieve it. The approved delivery plan and acceptance ledger are in this directory.

## Checkouts and review state

- Backend/desktop: `/Users/jainamshah/Documents/GitHub/termxmob`, branch `codex/desktop-workspace-v03`, published HEAD `1094609da6b390ec46438f9cd32f5cb3dfa1acad`, draft PR https://github.com/Beyon-Digital/TermXMob/pull/28.
- Client: `/Users/jainamshah/Documents/GitHub/termx-app`, branch `codex/managed-desktop-auth-v03`, HEAD `ee0a90f8303e41478e2233cc8bf6becfbf5e5827`, draft PR https://github.com/Beyon-Digital/termx-app/pull/26. Its CI `37638221463` passed. Preserve the unrelated untracked `aiux-test.mjs`, `probe-errors.mjs`, and `probe-page.mjs`.
- The current packaging repairs are intentionally uncommitted. Their local files persist. Review them before creating the next coherent checkpoint. No new artifact run has been dispatched for these repairs.

## Completion accounting

`acceptance.json` still has **32 verified / 6 partial** requirements. All ten feature families have implementation, but that does not establish complete installed-platform or live-provider delivery. Do not promote partial rows from source tests alone.

Partial rows: `UX-01` native unlock; `UX-02` native continuity; `DOCK-01` detach/re-dock and missing-monitor restoration; `VIEW-01` native window recovery; `MEDIA-01` live named media adapters; `REVIEW-07` evaluated/pinned smaller reviewer.

Rendered browser journeys, authorization, session-specific engines, browser handoff/takeover, durable execution, managers, Git and related source contracts have retained evidence. See the ledger and support matrix for exact qualifications. Hardware/enterprise/live-runner checks and operational restore notes must not be represented as performed tests.

## Latest published source gates — passed

Source `1094609`:

- Main CI https://github.com/Beyon-Digital/TermXMob/actions/runs/37692827265: frontend 245 passed; Linux backend 1089 passed / 147 skipped; macOS backend 1120 passed / 116 skipped.
- Windows platform CI `37692827271`: 247 passed / 1 skipped; native DACL checks passed.
- Native compile `37692827283`: all three platforms passed. Compilation is not installer qualification.
- `verification/ci-1094609.json` is a ready, untracked evidence file.

## Artifact run and concrete remaining failures

Run https://github.com/Beyon-Digital/TermXMob/actions/runs/37693649699 is for published source `1094609`. At this checkpoint Intel is still building; three other platform jobs have failed. Preserve exact source and artifact hashes in all future proof.

1. **macOS ARM**, job `113039789508`: frontend/backend, frozen sidecar, native contracts, browser/debug/accessibility checks and installer build passed. The app was signed, notarized Accepted (`9e63a972-a8cb-44be-b124-a5fcd02af2c5`) and stapled. Final DMG acceptance wait failed; the previous helper lost useful bounded diagnostics. Do not call this DMG qualified, infer a rejection/timeout cause, or resubmit the old DMG. Pending repairs persist safe known-ID diagnostics and reconcile only that same upload ID.
2. **Windows**, job `113039789586`: full backend 1093 passed / 127 skipped / 16 failed. Every failure is a macOS-helper synthetic private-key fixture incorrectly assuming POSIX chmod semantics on NTFS. All worker cancellation/startup cases passed. Pending repairs model mode 0600 only for the synthetic key; real macOS key restrictions remain unchanged. Installer/native GUI gates were not reached.
3. **Linux**, job `113039789528`: full backend 1124 passed / 112 skipped; frontend 245 passed. Exact AV wheel staging copied 32 libraries, clean dependency checks and the frozen sidecar passed. DEB/RPM/AppImage builds passed. The installed native GUI reached sign-in, then fixture cleanup invoked camelCase `deleteSession` although the real GraphQL schema exposes `delete_session`. Repair the fixture against the actual schema/arguments; this repair has NOT begun. The installed native journey is not qualified yet.
4. **Intel macOS**: installer build was still active when saved. Recheck its final status and retain small diagnostics. Historical runs repeatedly timed out detaching the DMG layout mount. Pending artifact-only native staging creates UDZO directly without that explicit writable mount, preserving signed/stapled app verification and final DMG signing/notarization.

## Frozen local repair ownership and verification

Read these companion checkpoints when they appear:

- `verification/browser-packaging-resume.md`: browser/packaging agent owns `.github/workflows/desktop.yml`, `desktop/scripts/notarize_ci_dmg.py`, `desktop/scripts/create_ci_dmg.py`, `desktop/scripts/artifact_matrix.py`, and their three direct test files. Final targeted subset: **64 passed in 16.51 seconds** (42 notarization, 15 container, 7 matrix). These are contract tests, not fresh installer proof. New CLI pin is 2.12.1. `qualification_scope` defaults all; release/tag paths must force all. Artifact-only Mac app bundle omits updater archive generation; production/tag behavior is retained. Root review is still required.
- `verification/windows-packaging-resume.md`: identity agent owns the ready `.github/workflows/windows-platform-tests.yml` path filters and inclusion of all three packaging test files, plus `verification/windows-artifact-109-backend-failure.json`. YAML/subset checks passed. No production/runtime edits.
- `verification/linux-packaging-resume.md`: durable agent retains exact Linux evidence and cleanup query diagnosis. No fixture repair was made before freezing.

No active test session remains from the packaging agent. Its read-only artifact monitor was session `84457`, directory `/tmp/termx-artifact-109-monitor-ea2kv15k`. Temporary log paths are supplementary; repository checkpoint files are the durable source for resumption.

## Live provider prerequisite

The user's answer was: **use the current active/chosen provider for the session**. Actual TermX session metadata and the effective resolver contain no selected API provider/model, media capability/pricing, reviewer model or spending cap. Native CLI/ChatGPT subscription access does not establish media API entitlement. No paid/live provider call has been made. The precise provider/model and numeric cap follow-up remains unanswered. Do not invent a fallback or treat elapsed time as approval.

## Update after resume (2026-10-08)

Run 37693649699 finished all-failed (Intel failed at Build installers). Frozen packaging repairs were reviewed and published with the Linux fixture fix (`delete_session(session_id:$id)`, validated against the real schema by a new test) as `4ec21f5`, then `31fcd1c` (first push failed CI on a `desktop` package import; fixed). Remote HEAD `31fcd1c270618833110859964db1d27f9e2974d5`. Gates on it passed: main CI, Windows contracts, native compile, macOS feasibility. Evidence: `verification/ci-31fcd1c.json`.

Artifact-only run https://github.com/Beyon-Digital/TermXMob/actions/runs/37708359897 (scope all, Intel included, empty release tag, draft true) finished overall **failure**. Mac ARM and Intel both passed end to end: container creation, final DMG Accepted, stapled, validated (hashes in `ci-31fcd1c.json`). Linux got through sign-in, monitor recovery, terminal transport and detached window, then timed out waiting for the detached placement file. Windows failed at WebDriver session creation (`DevToolsActivePort file doesn't exist`), the first time the Windows installed GUI has run. Neither cause is determined.

### Second repair (fixture only, no Rust/frontend change)

- **Linux placement file:** read `wry-0.55.1`/`tao-0.35.3` locally. A WebKit `close` (what WebDriver `DELETE /window` sends) only calls `webview.destroy()`; `tao` raises `CloseRequested` only from GTK `delete-event`. So the product never ran its placement save. Fixture bug, not a product bug. The fixture now closes the detached window with `wmctrl -i -c` (Linux) or `WM_CLOSE` (Windows), the same request a titlebar button sends. Title "TermX workspace" is unique to detached windows.
- **Later-step selectors:** a static audit against `desktop/workspace/src` found `[aria-label="Lock workspace"]` and `[aria-label="Stop host"]` do not exist. Lock is a visible-text status-bar button; Stop host opens from the Commands palette ("Stop this host", needs `host-admin`). Both now use the real controls. A new contract test checks every label, visible text and class the scenario uses against the workspace source.
- **Windows `DevToolsActivePort`:** best guess only, unverified. The fixture set `WEBVIEW2_USER_DATA_FOLDER`, which can hide the driver's port file; removed (`LOCALAPPDATA` already isolates the profile). Failures now write `diagnostics.json` (fixture tree, redacted log tails, relevant processes) into the uploaded GUI artifact.
- **Local validation before CI:** full backend suite 1154 passed / 117 skipped on macOS; fixture tests 15 passed (wmctrl parsing, fake-`user32` WM_CLOSE, diagnostics redaction, selector contract, cleanup mutation validated against the real GraphQL schema); workflow YAML parses; scripts compile. Not verifiable here: any Linux or Windows install behavior.

### Result of the second repair: run 37794420005 (source `407a3b3`, evidence `verification/artifact-407a3b3.json`)

Mac ARM and Intel final DMGs Accepted/stapled/validated again. **Linux installed DEB passed all 11 native GUI steps** (the detach close fix worked). Windows failed only at the backend suite (3 failures: two bugs in my new tests on Windows encoding/newlines, one flaky 3-second wait helper in `test_agent.py`); its installer and GUI steps did not run, so the Windows `DevToolsActivePort` guess is still untested. Follow-up pushed: test fixes, 15-second wait helpers, and a new `windows` artifact scope (Windows only, partial intermediate check; releases still force all platforms). The remaining plan gate is a green all-platform run on one SHA before `macos-installed-gui.yml`; `fetch_macos_installer.py` itself only requires the selected platform job to have succeeded.

### Windows-only run 37806173225 (source `b9fc453`, evidence `verification/windows-artifact-b9fc453.json`)

Windows now passes backend suite, native bridge, debug adapters, browser contracts, accessibility and both installers. The installed GUI still fails at `POST /session` with `DevToolsActivePort file doesn't exist`. The app itself is healthy in the diagnostics (window created, backend ready, WebView2/msedgedriver/tauri-driver all running), and the `WEBVIEW2_USER_DATA_FOLDER` guess was refuted. Next evidence needed before another guess: command lines of `msedgewebview2.exe` (does it carry `--remote-debugging-port` and which `--user-data-dir`), where any `DevToolsActivePort` file lands, and the msedgedriver/WebView2 versions. A cheap way to get it is a diagnostics-only scope that skips the 33-minute backend suite; that weakens the "complete gates" property so it needs the user's agreement and must never count as qualification.

**CI is a scarce resource (user instruction 2026-10-08): do not dispatch or push to iterate by guessing.** Each full artifact run is about 1h20m. Resolve causes locally or with a cheap targeted check first. Steps 5 and 6 below remain open; no installed-platform, live-provider or reviewer row has been promoted. The Mac installed-GUI workflow must not run against this failed source run.

### Windows installed GUI root-caused and repaired locally (source `77e56d6`, evidence `verification/windows-artifact-77e56d6.json`)

The whole `DevToolsActivePort` failure reduced to three independent causes, all proven and fixed on the Windows qualification box:

- **Driver session:** elevated WebView2 ignores `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` (documented upstream), so msedgedriver's `--remote-debugging-port=0` never reached the browser process. The workflow now writes the documented elevated escape — per-exe `HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments\termx-desktop.exe` — before the smoke and removes it in a finally block.
- **Terminal transport deadline:** fixture defect. ConPTY redraws the next `cmd.exe` prompt with a CUP escape instead of a line break, gluing the echoed marker to the prompt line. The fixture now treats CSI sequences as line boundaries; the strict line-equality assertion is unchanged.
- **Detach deadlock (real product bug):** `workspace_detach` was a synchronous command running inside the webview's `WebMessageReceived` COM call, and WebView2 cannot deliver the second controller-creation callback into that apartment (wry#583 / wry#1665; upstream fix PR #1666 is unmerged). Reproduced interactively without the driver: real-user detach produced a permanently blank window. The command is now `async`, which is wry's documented workaround; a locally rebuilt exe returns the label in ~0.4 s and both webviews initialize.

Full unmodified `native_gui_smoke.py` against a locally rebuilt `termx-desktop.exe` (same source, GNU toolchain) + the MSI-installed backend reports **passed:true for all 11 steps**. No assertion was weakened or deleted. One nit remains: fixture `TemporaryDirectory` cleanup can raise `PermissionError` on `agent.sqlite3-shm` when the backend still holds the WAL (intermittent, non-blocking).

**Still not proven:** the CI-built MSVC artifact itself (local proof used a GNU build of identical source; the change is a one-keyword signature change) and the workflow policy edit on the actual runner. One `artifactqualification_scope=windows` run is the next evidence, dispatched only after the push-time gates on `77e56d6` pass.

Work happened in worktree branch `worktree-v03-packaging-repair`, pushed to the PR branch by fast-forward. The main checkout still holds identical uncommitted copies of these files; they can be discarded after `git pull`.

## Resume sequence

1. Inspect current Git state in both named checkouts and these companion notes. Recheck Intel CI status. Do not discard frozen local repairs or sibling probes.
2. Review the packaging helpers/workflow changes, especially private-key handling, safe diagnostics, same-ID final Accepted checks, unchanged app hashes/signatures, and release matrix restrictions. Finish the Linux fixture query repair against the actual schema. Run meaningful targeted Python/YAML checks; no local Rust delivery build.
3. Commit/push only the reviewed coherent repair checkpoint and evidence. Await main, focused Windows and all-platform native compilation gates on the fresh remote SHA.
4. Dispatch exactly one artifact-only qualification run after those gates pass and remote HEAD is verified. Default to all platforms if Linux's native fixture repair requires a new Linux installed-app run; the proposed Mac-and-Windows-only scope alone cannot close the newly discovered Linux native failure. Empty release tag, draft true, Intel included. Keep exact per-platform source hashes.
5. After the new artifact run succeeds, qualify actual installed Mac GUI on ARM and Intel with strict final-DMG ticket, signature, Gatekeeper and source/architecture checks. Do not dispatch installed Mac verification against failed run 37693649699. Retain actual Windows installed MSI/native GUI and Linux installed DEB proof.
6. Refresh acceptance, support matrix, full HTML audit, delivery notes and PR body from actual results; preserve historical evidence. Complete live media/reviewer only with configured entitled provider/model and cap. Do not claim the whole plan complete while these gates remain open.

Nothing has been merged or released. Work can resume from this checkpoint without redoing the feature implementation.
