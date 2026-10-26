# TermX chat-first browser audit — 25 October 2026

The before walk used an immutable copy of the existing production bundle at
`/Users/jainamshah/Documents/GitHub/termxmob/desktop/workspace/dist`. Its files
and SHA-256 manifest were copied into the isolated proof folder. The host used
the original existing Python source, a separate configuration directory, and a
synthetic password owner. The original repository, installed app, conversations,
provider credentials and unrelated processes were not changed.

Evidence is under
`output/playwright/out/sessions/2026-10-25/baseline-201400/`:

- `report.json`: URLs and visible text for eight journey states, draft and
  overflow observations, page exceptions and external browser requests.
- `fixture-ui-snapshot.json`: the exact original bundle asset hashes.
- `frames/`: full screenshots at 1440 × 900 and 390 × 844.
- `sheet.jpg`: a preview contact sheet, limited to 1560 px on its longest side.

## Observed before behavior

| Journey | Actual observation | Implication |
| --- | --- | --- |
| Password sign-in | A fresh context showed `invalid CSRF token` before submitting a password. Synthetic password login then succeeded. | An ordinary signed-out browser was attempting an invalid refresh mutation. The account/runtime audit owns the API-boundary fix. |
| Root entry | Login opened `/manage/overview`, with Control plane, project portfolio, attention metrics and configuration destinations. | Chat was not the entry surface and no persistent main agent existed. |
| Start a chat | New chat opened a form containing title, project, preset and an absolute project folder, although an unassigned scratch conversation was supported. | Optional execution context occupied the main path into conversation. |
| Newly created chat | The selected session URL initially showed “Create a conversation and choose a project to get started” while its session read was still arriving. It subsequently displayed the ordinary chat prompt. | Session loading briefly looked like a different empty state rather than loading the chosen conversation. |
| Provider not configured | Chat showed a default model and a composer without a clear connection setup action. | A new user did not have a concrete next step before an attempted send failed. No send or live provider call was attempted in this audit. |
| Code context | Code remained a top-level peer to Chat and opened an empty Explorer/Terminal when no project was attached. | Development context competed with the primary conversation journey. |
| Chat → Code → Chat | The synthetic unsent draft was retained. | Preserve this existing behavior as a regression requirement. |
| Settings | Opening Settings and Escape returned to the selected conversation. | Preserve the conversation and its context across configuration. |
| Narrow layout | At 390 × 844 the navigation drawer/backdrop worked and the page had no horizontal overflow. | Preserve responsive navigation rather than replacing it without evidence. |

There were no JavaScript page exceptions and no external browser requests in
the captured walk. The browser shutdown handshake stalled after all eight
states were captured; the proof process was interrupted during teardown. This
was an audit walk, not a completed passing automated suite. Its host and test
browser were subsequently confirmed closed.

## Follow-up verification

`tests/workspace_chat_first_e2e.py` is the new opt-in production-bundle proof.
It uses actual host/authentication stores, approvals, child task creation,
conversation links, ownership handoff, direct interaction and cancellation.
Only provider plan/turn inference is replaced with an explicit no-network
deterministic fixture. Its browser has a separate temporary profile and an
exact owned process ID, with bounded cleanup; no broad process kill is used.

The script covers first-owner setup, a fresh configured-host password sign-in,
actual access-cookie refresh, one durable main agent, optional focused
chat context, draft switching/reload, actual delegated work, plan/tool gates,
child control and return, consolidated results, Stop, contextual Browser,
provider-test disclosure, a bounded real host restart and WCAG/overflow checks
in desktop, short and narrow layouts.

The first serialized execution checkpoint reached first-owner setup, automatic
main entry, connection guidance and the initial desktop WCAG audit (zero
violations and no page overflow). A fresh second browser context completed
password login into the same primary conversation without a false CSRF error
or an empty refresh mutation. No page exceptions or external browser requests
were observed through that checkpoint. The expected signed-out `/auth/me` 401
responses are recorded separately from JavaScript page errors.

Three incomplete attempts are preserved in `final-204000`, `final-204300` and
`final-204600`. The first two stopped on test-only assumptions about the
first-owner password label and `SessionIdentity.principal`. The third stopped
on the visible main button's longer accessible name; its capture also revealed
a real stale-session composer race during creation of a focused chat. The shell
owner is fixing that binding and the chat owner is reviewing the save-ack race.
Each attempt records successful bounded cleanup of its owned browser and host.

## Final frozen-source production proof

The full suite subsequently **passed**, against the parent's final production
build and actual current Python host source. The preserved report is
`output/playwright/out/sessions/2026-10-25/final-frozen-01/report.json`.

It completed **13 asserted journey checks**, **nine WCAG audits with zero
violations**, and **21 screenshots**. There were no page exceptions, horizontal
page overflow or external browser requests. The expected signed-out and
missing-access-cookie `/auth/me` 401 responses were followed by the supported
sign-in/refresh paths; actual refresh returned 200.

Verified through actual rendered controls and host state:

- First owner setup, fresh configured-host password login, one persistent main
  agent, provider connection guidance and optional focused-chat configuration.
- Independent drafts during rapid chat creation/switching, persistence on
  reload and no duplicate main agent.
- Real plan and delegation approval gates, real child task/conversation creation,
  direct child inspection, explicit control handoff, reload while work runs,
  direct follow-up and return to the main agent with consolidated results.
- A later child finishing after its parent turn produces a source-labeled inline
  report in the main transcript without another model call or a chat switch.
- One actual Stop request returns 200 and cancels its execution task; contextual
  Browser opening/closing preserves the main draft.
- Provider-test review names its destination, synthetic test request and possible
  charges; dismissing the review causes zero provider-test requests.
- Dark/light layouts at 1440 × 900, 1024 × 768 and 390 × 844, plus keyboard Escape
  restoring navigation-toggle focus.
- Actual access-cookie loss refreshes while retaining the account, main chat and
  draft. A controlled actual host shutdown/recreation on the same private port
  retains the managed session, primary binding, both child results/ownership,
  eight deduplicated activity IDs and the draft, without replay or inference.

The report records **nine deterministic inference-fixture turns**, zero live
provider calls and zero provider-test requests. These are simulated plan/turn
responses at the inference boundary; orchestration, authentication, stores and
HTTP/GraphQL handlers remain real.

The artifact directory's creation through final report took **50.7 seconds**.
Owned Chrome PID 2340 terminated gracefully, the private host stopped, and the
test process exited 0. No test/browser process remains active.

`review-sheet.jpg` presents six representative states at 1560 × 1548.
`evidence-summary.json` records every screenshot's dimensions, byte count and
SHA-256, the report checksum, cleanup facts and coverage limits. All original
frames and earlier incomplete attempts remain preserved. The full session
artifact folder occupies approximately **6.3 MiB**.

Native Tauri windows/bridge/keychain, real provider inference, native/ACP coding
engine sign-in, remote runners and mobile hardware are outside this browser
proof. The contextual Browser container and draft behavior were exercised;
managed guest page rendering/navigation, native webview and browser action
approvals were not. Plugin/skill live integrations and real computer capture or
input were not exercised. The restart proof uses terminal work; it does not
establish in-flight provider/process crash recovery. The fixture does not
establish real model quality, pricing, entitlement or provider access.
