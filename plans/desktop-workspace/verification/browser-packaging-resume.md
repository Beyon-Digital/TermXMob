# Browser and macOS packaging resume checkpoint

Frozen at the user's quota pause; no commit, push, workflow dispatch or package modification performed by this agent. Published base is `1094609da6b390ec46438f9cd32f5cb3dfa1acad`; repair candidates below remain uncommitted. Parent owns the coordinated source cut.

## Candidate changed files

- `.github/workflows/desktop.yml`: default `artifactqualification_scope: all`, optional `macos-and-windows`; tag/nonempty-release builds retain all platforms. Artifact-only Macs build the normal Tauri app bundle, then create a separate signed container without an explicit mounted layout. Pin official npm CLI `2.12.1` and log `tauri --version`; disable updater archive creation only artifact-only Mac. Upload safe pending diagnostic evidence even on failure; final installer upload still requires successful DMG ticket qualification.
- `desktop/scripts/notarize_ci_dmg.py`: keep strict macOS private-key mode validation; exactly one upload; persist known UUID/pre-ticket SHA and bounded phase/returncode/category before wait. Reconcile nonzero/unknown wait through structured `info` for the same UUID: exact-ID Accepted only; In Progress continues within3attempts/900seconds; invalid/rejected/auth/malformed/missing/foreign IDs fail closed. No raw tool output, argv or credential paths in reports.
- `tests/test_ci_dmg_notarization.py`: Windows models POSIX600 for only its synthetic key; only actual POSIXchmod qualification skips NTFS. Added realistic pending/Accepted/info/auth/rejection/foreign/missing/malformed cases.
- `desktop/scripts/create_ci_dmg.py` and `tests/test_ci_dmg_container.py`: require matching signed/stapled/Gatekeeper-approved Tauri app and architecture; ditto preserving metadata into a private staging directory; compare full app/binary hashes; Applications link; native compressed `hdiutil create -srcfolder` without explicit attach/detach/Finder phase. Sign only new container, verify signature/integrity; no qualified receipt until separate final-DMG notarization. Partial output removed on failure. Windows symlink orchestration is explicitly modelled; real POSIXlink boundary skips NTFS.
- `desktop/scripts/artifact_matrix.py` and `tests/test_artifact_matrix.py`: bounded explicit platform selection; releases force all; selected platforms retain complete backend/native/UI gates.
- Identity separately owns `.github/workflows/windows-platform-tests.yml`, adding all three new test/script families to triggers and hard checks. Coordinate that file in the same source cut.

## Completed targeted verification

`.venv/bin/python -m pytest -q tests/test_ci_dmg_notarization.py tests/test_ci_dmg_container.py tests/test_artifact_matrix.py` → **64 passed in16.51s** (42notarization,15container,7matrix). This is synthetic orchestration/contract evidence on macOS; actual Windows rerun and real signed container qualification remain pending. Python compilation of the three helpers passed. No local Rust/native build, Apple submission or paid provider call occurred. No running test sessions; session93506 completed.

Official npm metadata queried read-only: CLI2.12.1, gitHead `30da1fd6e17de6107ecc850c95dfb16b5729f2dd`, integrity `sha512-kEDEiGzG+yAc5FeLxtXpES/VN+F2C8H0r4gDVtfRLKxT9np9101d9REG/Kgo2lr0hKi0IpDsODiHnU3+naOmLg==`. Prior workflow used CLI@^2; RustTauri2.11.5 did not establish its resolved CLI/bundler version. Official tag-script diagnostic in /tmp is explicitly not proof of the old resolved version.

## Current immutable CI evidence

Base109 source gates: mainCI37692827265, Windows37692827271, nativecompile37692827283; parent verified successful gates before single artifact dispatch. Artifact-only run **37693649699**, run38, exact109, emptyrelease_tag. Observation `2026-10-07T22:53:20.006985+00:00`:

- prepare: job `113039757658`, completed/success, stage none, failed stages none.
- macos-arm64: job `113039789508`, completed/failure, stage none, failed stages Notarize and verify final artifact-only macOS DMG.
- macos-x86_64: job `113039789512`, in_progress/pending, stage Build installers, failed stages none.
- linux: job `113039789528`, completed/failure, stage none, failed stages Verify installed Linux native workspace.
- windows: job `113039789586`, completed/failure, stage none, failed stages Verify complete backend suite.

ARM113039789508: app Accepted `9e63a972-a8cb-44be-b124-a5fcd02af2c5`/stapled; container built/signed; final DMG acceptance wait failed. Helper22:28:21.166→22:32:53.646 (~272.48s), public `DMG acceptance wait failed`. No submissionUUID/private diagnostic was retained by base109; do not assert timeout/auth/rejection cause. Installer/receipt upload gate blocked. Log253903bytes SHA `e1e30357e898aca3cb6a97136856faccba0b23b6adaa168596688260ddfad8db`. Earlier ARM245frontend/1122backend/native17+1/debug14/browser29passed;114backend skips/6debug skips remain limits.

Windows113039789586 failure consists16 Mac-only helper synthetic permission-fixture failures (POSIXmode on NTFS); actual macOS guard must not be weakened. Backend1093passed/127skipped/16failed; log `/tmp/termx-windows-37693649699.log`. Candidate fixture repair has not run on Windows yet.

Linux113039789528: DEB/RPM/AppImage packaging passed; installed-GUI cleanup failed on GraphQL `deleteSession` (actual schema exposes `delete_session`). Durable reported this concrete fixture gap; **not fixed** at pause. Relevant source `desktop/scripts/native_gui_smoke.py` around246–247 still has camelCase query; verify actual argument/return names and add meaningful live-schema regression before next installed qualification. Durable owns Linux packaging, which included32AV libraries. Do not duplicate large downloads.

Readonly109 watcher was session **84457** (stopped for quota pause), script `/tmp/termx-artifact-109-monitor-ea2kv15k/watch.py`; polls60seconds, emits changes only, saves latest/final/artifact metadata. No large installer downloads. Restart the read-only watcher on resume if useful; no local monitor remains running. No actual installed-Mac workflow has been dispatched.

Old7f run37687927763 finalCANCELLED by parent; ARM113020487467SUCCESS signed-onlyDMG, WindowsbackendFAILURE, Intel113020487452CANCELLED (not final packaging success/failure). Intel first appAccepted `a990785c-4105-4422-bbc1-b7cce91e7363`/stapled, then owned disk4detach120-second DiskArbitration timeout despite skip-jenkins; bounded rebuild appAccepted `262a2775-2fb7-4653-86cc-a2af368c32f7`/stapled then cancelled during secondDMGcreation. No Intel installer artifact. Old watcher97440 exited.

## Evidence and incomplete next steps

- Compact current evidence/logs: `/tmp/termx-artifact-109-monitor-ea2kv15k/` (`latest.json`, `changes.jsonl`, `arm-job.log`, `arm-dmg-wait-failure.json`, contingency/source-contract reports). Small old final Mac history: `/tmp/termx-artifact-7f-monitor-CCWOU7/macos-artifact-7f-final-history.json` and `.md`; parent can copy after pause without changing historical hashes.
- Candidate packaging source review/diff-check and coordinated source checkpoint are still pending; no fresh sourceCI or artifact run contains these repairs. Validate new source on actual Windows and bothMacs. Test new bounded diagnostics/metadata assertions fully on cleanCI; no missing result should be claimed as zero/pass.
- Fix exact Linux installed-fixture GraphQL cleanup mismatch, preserving real native authentication and renderer flow. Retain109 Linux packaging proof distinct from installed failure.
- Preserve full-platform source CI. A fresh Linux installed-app run is required after the native fixture correction: retained109 packaging proof and a Mac-and-Windows-only artifact run cannot complete all-platform qualification. The narrower matrix is available only for explicitly partial intermediate checks.
- Actual installed-Mac gate is still open. Only once the next exact-source artifact run is overallSUCCESS and fresh remote head matches that source may the authorized `macos-installed-gui.yml` workflow_dispatch run with its source_run/source_sha. Old7f/109 are **not dispatchable**. PRfixture jobs never substitute for actual installedDMG/Gatekeeper/ownedAX lifecycle.

## Safety constraints for resume

No Git mutations by child, no dispatch/cancel without coordinated parent gate, no local Rust builds, releases/updaterpublication, paid provider fallback, externalbrowserUI, large installer downloads, privacy overrides, force/unrelated unmounts, globalOSprocessremedies, quarantine removal, old package rebuild/staple/resubmit or unknown-ID upload replay. Strict Accepted/sameID/signature/stapler/hdiutil/exact-hash gates remain required. Candidate helper/staging changes are unqualified until actual CI; unchanged installed-package fetch gate is retained. Account/live-reviewer and native hardware/OS-permission gates remain honestly open.
