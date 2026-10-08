# Historical 7f macOS artifact packaging

Source `7f1160f628c74499fbcf04be1ba7cb65b2788d55`, artifact-only run [37687927763](https://github.com/Beyon-Digital/TermXMob/actions/runs/37687927763). Observation: 2026-10-07T21:50:16.389206+00:00. Exact machine-readable evidence is in `macos-artifact-7f-history.json`.

ARM job **113020487467 succeeded** at 21:37:32 UTC: 245 frontend tests / 64 files; backend 1087 passed / 114 skipped; native 17 passed / 1 ignored plus separate OS-keyring test passed; debug adapters 14 passed / 6 skipped; managed browser/native review 29 passed.

The app was signed, notarized **Accepted** (submission `32ff1362-7bfb-4e61-9144-f0e96e7cb951`) and stapled. The final `Termx_0.2.6_aarch64.dmg` was subsequently created and signed. **The final DMG was not submitted or stapled by this workflow.** The strict installed-package fetch gate requires a DMG ticket; ARM packaging success therefore does not qualify the installed-Mac workflow. Existing packages remain untouched. The follow-up introduces a distinct artifact-only DMG submission/staple/validation step; it cannot retroactively qualify this run.

Installer artifact `termx-macos-arm64-37`: ID **11513476127**, **477503046 bytes**, GitHub upload ZIP digest `5942d06b357dab4306f3a961bc97f518c7a64ef9bdd3950be98af7dc746fdbb8`. This is archive metadata, not a downloaded DMG hash. ARM job log: 267116 bytes, SHA-256 `21a4dc40ea8501cb5b0253aeb9db93355a4179e4d3c61b93b005455ae6ef33bc`.

Intel job **113020487452** is independently `in_progress`; current stage `Build installers`, conclusion `None` at the recorded observation. No Intel installer metadata or result is inferred from ARM. Windows job **113020488028 failed** its complete backend suite; Linux remains independent. Actual installed-Mac dispatch is held. No paid provider calls, local Rust builds, large installer downloads, release/updater publication, privacy overrides, or modifications to these uploaded packages occurred.
