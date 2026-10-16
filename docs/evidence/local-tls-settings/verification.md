# Verification record

Executed 2026-10-15/16 in the provisioned Linux TermXMob environment. Both repositories were clean before work and matched fetched `origin/main`: TermXMob `ce5cad9321d56f6060f1272a072b9f710cb6303e`; termx-app `18ef066`.

No AGENTS.md or software-engineering skill was available in the provisioned repositories or installed skill tree. Repository testing instructions were read. Existing remote branches and PRs were inspected; the companion carries forward draft #26 on current main with conflicts resolved, preserving the newer task routing and background-observation changes.

## Results

| Check | Exact result |
| --- | --- |
| `npm --prefix desktop/workspace run build` | Passed (TypeScript project build + Vite). Existing bundle-size warning remains. |
| `npm --prefix desktop/workspace test -- --reporter=dot` | **325 passed**, 77 files, 38.13 s. |
| `uv run pytest -q tests/test_lan_tls.py tests/test_managed_pairing.py tests/test_identity_guard.py tests/test_identity.py tests/test_cli_banner.py tests/test_desktop_bridge.py tests/test_host_service.py tests/test_host_service_http.py tests/test_host_service_runtime.py tests/test_private_files.py` | **111 passed, 2 skipped**, 31.96 s. Skips are Windows-only ACL tests. Existing Starlette/asyncio deprecation warnings. |
| `tests/test_lan_tls.py` | 21 tests within the host total: real verified HTTPS/WSS, CA/SAN/auth/Origin rejection, identity persistence/renewal/rotation, private/symlink storage, remote plaintext, staged admin configuration and supported advertisement. |
| `python tests/workspace_settings_tls_e2e.py /tmp/termx-settings-evidence` | Passed against an isolated real host and `/usr/bin/chromium`; all 14 categories, memory CRUD protection, confirmation cancellation, staged TLS settings, focus trap/return, URL behavior. No paid provider traffic. |
| Responsive evidence | 1440×1000, 768×900, 390×844; light and dark; no page overflow. Mobile chip navigation overflow scrolls. See `report.json` and screenshots. |
| Axe 4.10.3 | Zero violations in the tested settings dialog at 1440 and 390 px for WCAG 2 A/AA and 2.1 AA tags. |
| Companion `pnpm exec tsc --noEmit` | Passed. |
| Companion `pnpm test` | **84 passed**, 11 files, 1.20 s; includes 12 new LAN transport tests and the carried-forward managed-auth tests. |
| Companion `expo export --platform web` | Passed; output held only in `/tmp`. |
| Expo autolinking resolve | Both Apple pod/Swift module and Android local module discovered. |
| `git diff --check` | Passed in both repositories. |

The standard Playwright browser download was forbidden by the environment's CDN policy. The installed `/usr/bin/chromium` was used successfully; no TLS ignore flag, root import, firewall change, OS trust setting, live service installation or deployment was performed. Test identities/accounts and grants were temporary fixtures under `/tmp`.

## Remaining platform gates

No Swift/Kotlin compiler, Xcode/iOS SDK, Android SDK/Gradle, mobile devices, macOS desktop or Windows desktop is available here. Native compilation and installed-device TLS/camera/secure-store/file/LSP/PTY/computer/background/touch/keyboard/safe-area execution are unverified. Native drafts must remain unreleased until positive and adversarial TLS tests pass on those platforms. JS mocks, autolinking and browser screenshots do not qualify them. No CI reruns or release/deploy jobs were requested.

## Review follow-up (2026-10-16)

- Initial companion CI passed type checking, all 84 tests and web export. Initial host workspace CI passed all 325 tests, production build and rendered accessibility/updater checks; native media-policy Rust compilation passed on Linux, macOS and Windows.
- Host full Python CI found an obsolete native smoke selector on all platforms. The smoke now opens Home then uses the retained Home view tabs, and opens canonical General Settings before signing out. Windows owner-DACL proof passed. The single macOS automation timeout passed locally (25 native-fixture/autonomy tests); its next CI execution remains the arbiter.
- The independent TLS review hardened SAN validation during renewal, rejects future-dated persisted identities without replacing them, handles malformed Origins as denials, and preserves the admitted WebSocket session binding across GraphQL initialization. Targeted security/native-fixture regressions: **59 passed**, 6.53 s. Native fixture/autonomy checks: **25 passed**, 2.96 s.
- Settings now follows `visualViewport` resize/scroll for mobile software keyboards; its eight focused tests and TypeScript/Vite production build passed. The bounded real-host responsive proof passed again after this change with zero page errors and zero tested axe violations. Physical-device keyboard/touch behavior remains unqualified. CI now runs this responsive proof and retains its artifacts.
- Companion native policy source was compiled with real Apple Security APIs on macOS CI: valid chain accepted; wrong CA/SAN/hostname, expired/future leaf/anchor and fingerprint mismatch rejected. The actual Expo Swift/Kotlin compilation jobs were added separately; initial setup failures (obsolete SDK tools package and missing pinned Skia binaries) were corrected. Consult the PR's latest checks for their final result. Companion review regression: 13 transport tests passed; TypeScript passed.

These records distinguish local/browser and native-framework proof from installed-app acceptance. No installed iOS/Android app, native GUI or OS browser trust journey is claimed.

The second form audit found that validation/preview/test POSTs and GraphQL reads could incorrectly clean a form. They now retain dirty state, as do non-refreshing manager inspections. Mutation cleanup is bound to the initiating resource row so another editable form remains protected. Seven regression cases cover these boundaries; nested editor close/load/restore actions use the shared discard guard.

At host head `c8929773e32166f1a45abe853f79b6b45d47c91e`, CI passed: Linux Python **1353 passed, 176 skipped**; macOS Python **1384 passed, 145 skipped**; Windows contracts **333 passed, 3 skipped** plus three owner-DACL Rust tests; workspace **326 passed / 77 files**, build and all rendered proofs; Tauri platform source compilation passed on Linux/macOS/Windows. The previous isolated macOS automation timeout did not recur. The next head contains the additional form regressions; its own checks supersede these results.

At companion head `eb6976a2afb3ed2ad42b7145174d581ad106250f`, app CI passed TypeScript, **88 tests / 12 files**, and web export. Real Expo Swift module compilation passed for **arm64 and x86_64 iOS simulator** targets, in addition to the Apple Security trust-policy tests. Android module compilation remained running at this record. Installed-device acceptance remains outstanding.

Additional form verification: **22 focused tests passed** across settings surface, managers and memory editor; the production build passed. The real-host responsive/axe proof passed again, now including successful skill validation followed by refused navigation with the unsaved instructions intact. This uses no paid providers or persistent host state.
