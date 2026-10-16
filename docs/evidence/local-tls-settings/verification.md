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
