# Browser transaction and challenge boundary

This is source and real managed Chromium fixture evidence against the unchanged v0.3 HTML. It covers PLAN-T09 R07 (manual challenges), PLAN-T13 R05 (readable approval target/data/consequence), PLAN-T14 R02 (exact purchase evidence), and the scoped-origin browser boundary. No paid model query, external website, real purchase, login account or user browser was used. The shared frontend component tests are separate from a production theme/zoom renderer matrix; this report does not qualify a new production UI bundle or installed native application.

`browser/challenges.py` reads bounded host-observed visible semantics, named provider frames and sitekey markers before classification and immediately before queued click/type/upload effects. Recognized CAPTCHA, human-verification and MFA/one-time-code continuations require takeover/private login; remembered consent, a prior exact permit and the smaller reviewer cannot solve them. Explicit human challenge solutions are excluded from recordings even when the control disappears after the click. Ordinary noncustom checkbox editing continues to work. This is conservative recognition of the tested controls, not detection of every provider, language or anti-bot mechanism.

`browser/approval_preview.py` supplies a bounded human-facing operation, website/destination, profile, page-displayed account, target, submitted fields and consequence. Purchase evidence requires merchant, amount and currency. Page labels and transaction metadata are explicitly untrusted; a Chromium profile name is not proof of merchant/account authenticity. Password/card/token/OTP-labelled values are redacted, hidden protocol fields are never copied into the preview, and CSS/semantic-hidden nonhidden submitted controls make the operation incomplete/manual. Marker lookup cannot use an invisible merchant/account/charge field. Root `data-*` transaction metadata is explicit untrusted page metadata, subject to the same redaction and exact document hash.

The preview binds to the existing target/form/document hash, which includes transaction evidence and form state but persists only a digest. It is checked after preview collection and again before the effect. Amount or target changes require fresh review; the prior single-use decision cannot execute the changed operation. Missing, truncated, sensitive or unknown effects cannot be approved by the agent route and expose an explicit takeover path. BrowserSurface and the shared Chat approval card display the same evidence. Upload/download references and diagnostics keep their separate explicit transfer boundaries; this collector is not claimed to preview every export.

Agent-controlled document/fetch/XHR and nonread requests must stay inside the live grant origins. Passive image/style/font/script resources retain their separate network policy. Automatic grant revocation retains a per-tab transport restriction, so a second page-script request cannot silently become a human request. Redirects, adopted popup retries and initial popup requests without frame attribution retain this boundary. Explicit authorized takeover/human input or a fresh validated handoff is required to recover. The UI names the retained pause and provides Take over. These checks do not predict arbitrary JavaScript consequences or rule out every possible covert channel through passive assets, preexisting transports or custom browser content. Inline/property-handler checkbox controls are classified unknown/manual; arbitrary delegated/registered listeners are a declared recognition limit.

Final focused checks:

| Check | Result |
|---|---|
| Exact purchase evidence, charge-change rejection, single effect/dedup, missing/sensitive/manual evidence, and current managed browser stale/private contract | 3 passed in67.61s |
| Recognized CAPTCHA/MFA/sitekey/provider-frame/one-time-code, late challenge, ordinary checkbox and manual recording exclusion | 2 passed in23.61s |
| Hidden submitted inputs (`display:none`, `visibility:hidden`, `opacity:0`, `hidden`, `aria-hidden`) and invisible merchant marker | 1 passed in52.91s |
| Ancestor opacity-hidden input excludes value/account and performs no effect | 1 passed in15.17s |
| Final retained repeated POST/popup retry, passive image, explicit human recovery and redirect boundary | 2 passed in12.48s |
| Shared operation preview/native nested approval plus existing Browser/card regressions | 23 passed in55.34s |
| Blocked manual operation offers takeover without approval/handoff | 1 passed in4.65s;13 unrelated cases intentionally not selected |
| Retained network pause is named and released only by explicit takeover | 1 passed in3.09s;14 unrelated cases intentionally not selected |

Commands are recorded with source hashes in [browser-transaction-boundary.json](browser-transaction-boundary.json). Each real Chromium fixture uses a scratch host/profile and localhost-only sites. The challenge-provider URL marker is locally aborted; no provider is contacted. The first broad run had34 passes and one passive-asset fixture failure because the fixture served HTML as an image and Chromium blocked it; the corrected valid SVG fixture passed the final targeted checks above. That broad run is not represented as a final full-suite pass.

`git diff --check` passed after the source freeze. Root owns the subsequent coherent production build and full source CI. Live reviewer qualification (REVIEW07), installed/native permission and physical browser/input/capture checks remain distinct open gates.
