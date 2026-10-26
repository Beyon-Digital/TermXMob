# Native coordination browser proof

Status: **passed** against the final production bundle. Run `native-browser-03`
completed 7 journey checks and 6 accessibility audits with zero violations or
overflow, 10 screenshots, zero page errors and zero external browser requests.
It made 3 fixture-native dispatches and resolved 1 exact native permission request.
Live provider calls and native vendor processes were zero. Host and browser both
stopped; elapsed 25.32s. Evidence:
`output/playwright/out/sessions/2026-10-25/native-browser-03/report.json` and
`review-sheet.jpg` (1560x1580, visually reviewed; no credentials shown).
The completed `89fb066` browser proof remains a separate baseline.

Initial attempt `native-browser-01` failed during fixture initialization before
starting the host or browser: the harness accessed `state.workspace` before
`create_app` mounted it. The failure is preserved in
`output/playwright/verification/native-browser-01.log`. Session setup now follows
`create_app`; the adapter also marks completed bindings idle before hot reuse.
These harness corrections preceded the passing runs. Run 02 passed; run 03 also
waits for the final Main conversation to load and verifies actual cancelled/idle
child cards before capturing its closing frame.

`tests/workspace_native_orchestrator_e2e.py` exercises the production React
bundle against an isolated real AppState, authenticated HTTP/GraphQL, Internal
Agent tool loop, EngineGateway and persistent task/session/approval stores.
Main inference is deterministic. The `fixture-acp` engine is an **in-process
normalized EngineAdapter fixture**, not a vendor executable or ACP wire-protocol
implementation. Separate backend protocol tests cover the repository's Python
stdio ACP helper. Neither boundary establishes real vendor sign-in or inference.

The passing journey signs in, establishes the main agent, approves its plan and
native delegation, opens the child, takes direct ownership, and reviews the
actual native source permission. It verifies progress/result delivery and the
same native session ID for direct follow-up. It then approves enrollment of an
existing idle chat at the exact reviewed revision, checks that enrollment starts
no task, and explicitly returns ownership to the main agent. Capability details
must state unsupported steering/resume, unmeasured token/cost usage, and actual
host-monitoring limits.

The final native turn produces partial output and waits for Stop. After actual
Gateway cancellation and reload, the browser must preserve the partial result,
the exact persisted `Interrupted` diagnostic, user ownership, and one dispatch
receipt. The stopped response must use `Task stopped` with `role=status`; it must
contain neither the generic error caption nor an error/alert in that message.
Desktop and 390 × 844 captures and WCAG checks include this cancellation state,
the idle adopted chat, capability disclosure, and consolidated main view.

After grant and build, use a new output directory:

```sh
PYTHONPATH=src /Users/jainamshah/Documents/GitHub/termxmob/.venv/bin/python \
  tests/workspace_native_orchestrator_e2e.py \
  output/playwright/out/sessions/2026-10-25/native-browser-01
```

The harness starts one private loopback host and one known Chrome process with
an isolated profile. Browser requests outside that host are blocked and recorded.
The production bundle is copied before launch and every asset hash is retained
in the report. Cleanup addresses only those owned resources. New output folders
preserve reports, failures and screenshots; no old evidence is removed.

No Rust, paid/provider calls, vendor processes, real credentials, deployment or
GitHub CI is involved. Tauri/keychain integration, real provider authentication,
guest-browser actions and live native platform behavior remain untested here.
