# Live provider qualification prerequisites

Read-only inspection at 2026-10-07 19:20 UTC used actual daemon and native-desktop provider metadata, including SQLite WAL. No credentials were retrieved, no accounts or capabilities were changed, and no provider requests were made.

The daemon has a configured ChatGPT subscription account declaring coding capabilities, plus an unconfigured localhost API account. Native desktop has configured OpenRouter (`openrouter/free`) and a local compatible Kiro proxy; both declare computer/shell capabilities. These declarations and credential flags do not establish image/audio entitlement or a working exact reviewer model. The latest persisted sessions have no explicit provider/model selection, and no native engine model override is configured.

No preexisting numeric qualification allowance, media capability declaration, active reviewer configuration or administrator pricing snapshot was found. MEDIA-01 requires an explicitly selected entitled API account/model and bounded test allowance. REVIEW-07 requires the exact reviewer account/model/version and pricing snapshot before the frozen 21-case evaluation can report real identity, usage, cost, latency, escalation and false-allow results. The current ChatGPT app or subscription session cannot be treated as an API billing entitlement. No fallback or paid query was attempted.

Read-only review of the candidate BudgetApproval and GeneralApprovalCard changes found no additional high-impact lifecycle issue: lock/sign-out close and disable dialogs, retained limits/scope survive close/unlock, and identity/generation fences reject old replies and unmount callbacks. The App synchronously bridges session-locked into the lock event. This is a source review; existing regression sources were inspected, not rerun here. Backend current authority remains the enforcement boundary for already-dispatched requests.

Source base: `44320592c6880721f82d6369b1942a031f8ec825`. Modal changes were unpublished candidates at inspection. Installed GUI, real API entitlement, live reviewer qualification and actual spend remain unverified.
