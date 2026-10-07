# Desktop workspace rollout and recovery notes

These notes accompany source checkpoint `7f1160f628c74499fbcf04be1ba7cb65b2788d55`. [Source CI evidence](verification/ci-7f1160f.json) records245 frontend,1054 Linux and1085 macOS tests passing; Windows contracts and all three native compile targets pass. Exact-source installer qualification is recorded separately. Live media/reviewer qualification still requires the chosen entitled API account/model and a bounded evaluation allowance. No production release or updater publication has occurred.

## Upgrade preparation

1. Record the current application version, host ID, configured adapter file, active runner/task IDs and Git working-tree state. Preserve independent window drafts and dirty editor/artifact buffers through their existing save/export or acknowledged re-dock paths. While an authenticated owner session is available, provision and securely preserve the owner recovery code through `/auth/admin/recovery-code`; recovery requires this preexisting code and has no bootstrap fallback.
2. Stop task admission and unattended schedules, then explicitly finish or cancel active runs and dedicated runner leases. Keep the completed-effect and approval ledgers. Stop the host before taking a consistent private backup of its complete configured data directory, including SQLite WAL state, browser profiles and artifact versions. Keep provider credentials and native refresh credentials in their existing protected stores; backup manifests contain references rather than secret values.
3. Preserve project files and compatible extension sources separately from app data. Install the matching qualified package, retaining its source SHA, package hash, platform, signature/notarization outcome and immutable CI evidence. Delivery binaries come from CI.

## Managed authentication and data migration

The identity service uses `identity.sqlite3`; execution and workspace state use `agent.sqlite3` and `workspace.sqlite3`. Startup adds the managed-session, group and budget structures required by this implementation. First-owner setup remains local and same-origin. Activation immediately ends passcode API login; existing paired devices retain their original scopes only through the persisted seven-day migration deadline. Restarting the host does not renew that deadline. Deploy the companion managed-auth mobile client before expiry.

Identity adapters retain explicit issuer/subject bindings. Group claims require the pinned issuer and mapping; email does not merge identities or grant roles. Device lifetime reductions apply to existing sessions; increases do not widen their stored caps. Confirm password/SSO, current-role revocation and managed refresh on the intended deployment before opening remote access.

Use a fresh sign-in and inspect projects, conversations, dirty buffers and device policy after upgrade. Parked tasks and crash-orphaned budget leases require explicit continuation or renewal. A committed grant receipt resumes within the same version and allowance; it must not create another grant or repeat completed effects. Reconnect keeps each run's engine, model, location and authority rather than retargeting it.

## Recovery and rollback limits

An automatic schema downgrade or old-binary/new-database combination is not qualified. Recovery starts with the stopped host and a matching application/data snapshot. Keep recovery local until identity, project roles, extension permissions, runner grants and task receipts have been inspected. A restored snapshot can contain obsolete sessions and consent, so revoke restored principal sessions with the current administrator session controls and end any restored legacy migration before restoring remote admission. Owner recovery uses the existing local same-origin recovery-code flow.

Restore drafts and project files deliberately; retain conflict copies and the completed-effect ledger. Reconcile uncertain tool/media/Git outcomes before retrying them. Re-enroll affected runners and grant fresh task-scoped browser/Computer control. Avoid restarting restored unattended schedules until their target, authority and budget have been reviewed.

These are operator recovery notes, not a performed downgrade or restoration test. Exact installed-package and live-account acceptance remain named gates in the support matrix and acceptance ledger.
