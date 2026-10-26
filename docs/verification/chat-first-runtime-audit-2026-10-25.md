# TermX runtime and account setup audit, 25 October 2026

Scope: isolated clone at `Documents/Codex/2026-10-25/task-6/termx`, based on the current local `termxmob` source including preserved existing workspace edits. Original repositories and running unrelated applications were not changed.

## Findings and implemented changes

| Journey | Observed issue | Implemented behavior |
| --- | --- | --- |
| Connect an API provider | The first screen exposed an API address and all declared capability switches. It did not distinguish an API account from a signed-in coding engine. | Guided OpenAI, OpenRouter and custom compatible endpoint selection, with coding-engine guidance. Model remains available for everyday setup; capability and internal-key controls are under Advanced account options. Existing custom endpoints and declarations are preserved. |
| Save a credential | Key remained in the form until a successful response; asynchronous rejection could escape without local feedback. Two rapid submissions could dispatch twice. | Key clears on submit and when changing its provider destination. An in-flight guard prevents duplicate dispatch. A failed save shows its error and reuses the generated account ID on retry. A successful creation resets the name and generated ID for the next distinct account. No key is written into local storage or conversation history. |
| Test a provider | The button named Test connection invokes `ResponsesAdapter.test()`, which submits `Reply with only OK.` to the configured model. This is an inference request and can incur charges. | A review dialog names the destination, model, exact synthetic message and possible charges. Cancel sends nothing. Missing default models block test submission. Failed structured responses do not display success. The review does not establish a provider's price or free entitlement. |
| Fresh browser sign-in | A configured host attempted a refresh POST despite no retained CSRF proof, so an ordinary sign-in screen displayed `invalid CSRF token`. | The refresh boundary checks current access first, then skips empty refresh requests. It returns the normal sign-in state without broadcasting sign-out or discarding requested chat routes. Existing expiry refresh and transient-error handling remain intact. |
| Sign in / first owner | Password setup and managed-cookie flow are supported. Host-passcode helper copy said the passcode is printed at startup, while CLI redacts it. | Reported to the shell owner for copy correction. No passwords or authentication policy were changed in this subtask. |
| Local test startup | Local testing skill refers to `/api/health`, which the current app no longer serves. | Confirmed `/auth/methods` returns 200 and first-run `configured:false`; `/api/health` returns 404. The health schema lives under GraphQL. Curl on this Mac requires `--noproxy '*'` for loopback. |

## Verification

`npm test -- --maxWorkers=1 src/features/managers.test.tsx src/features/provider-account-form.test.tsx src/features/settings-surface.test.tsx` passed **33 tests across three files**. Checks cover provider presets and destination changes, immediate write-only secret clearing, duplicate-submit protection, stable retry IDs, existing configuration preservation, concrete billable-test review, cancellation without dispatch, failed-result feedback, settings draft preservation, mobile viewport behavior, and guarded category changes.

The final provider-form rerun passed **4 tests**, including creating a second distinct account after success. The API-boundary rerun passed **21 tests**, including fresh-browser sign-in without a refresh mutation or sign-out event, valid access with no readable CSRF cookie, expired-access refresh, transient failures, locking, origin restrictions, uncertain mutation results, and non-replayed GraphQL mutations.

The isolated Python host runs on `127.0.0.1:8797`, with `TERMX_CONFIG_DIR` set to task-local `runtime-state`, `TERMX_ENGINE_STARTUP_REFRESH=0`, and no tunnel. It uses the existing Python venv and current isolated source. No Rust compilation, native rebuild, GitHub CI, deployment, new external credential, or live inference was performed.

A sandboxed bind first failed; the explicitly authorized loopback host then started with execution escalation. No broad process kill was used. The owned listener was PID 90293 when launched; confirm the current PID before any targeted shutdown or restart.

## Reference and coverage limits

The current CUA inventory exposed no enabled apps or browsers. Native Codex and native TermX inspection were therefore unavailable through that supported route. The existing workspace design notes cite the [official Codex 101 workshop](https://d2xo500swnpgl1.cloudfront.net/uploads/oaiacademy/Codex-101-Introduction-Demo-32ffbb3c-85da-4da9-973d-5b39ea7d39fd-1773866180169.pdf), which was retrieved for source verification; this does not establish parity with the installed October Codex interface. No Grok reference behavior is claimed.

Provider and credential tests use mocks/synthetic keys. Native keychain, installed engines' real sign-in, live model results, model pricing, physical device and native bridge steps remain untested in this subtask. Full browser flow and orchestration evidence are owned by the parent task and recorded separately.
