# Final production transaction, manual takeover and paused-request UI

**Passed against immutable `index-BIqFMukh.js` / `index-Bhlp77nW.css`.** The actual managed host and two localhost sites exercised 16 Browser/Chat/manual/network states in dark/light at 100%/200%, 129 actual Tab/focus checks, 48 fully readable transaction fields/hash checks and 4 exact review-button focus returns. Every measured button and its focus ring fits the viewport and clipping ancestors; zero clipped rings remain. All states have reduced motion, no page JavaScript error, horizontal overflow or axe WCAG2/2.1 AA violation. The [full report](browser-transactions-rendered-BIq.json) preserves 66 asset hashes, 24 original screenshots, geometry, incomplete axe findings and 12 unchanged scoped source fingerprints.

| Actual working state | Exercised result |
|---|---|
| Browser approval | Visible merchant, 12.50 EUR, submitted order item, profile, page-displayed account and exact document hash. Each field is independently scrolled fully into view and checked for uncovered reading. Approve once/Deny have complete visible focus rings. Hidden protocol values are absent. |
| Chat approval | Actual canonical approval opens Review browser operation. The bounded dialog shows the same exact transaction; Allow once/Deny and Close are fully reachable. At 200%, the 425px dialog fits the 720×500 CSS viewport and scrolls normally. Closing returns focus to the exact review trigger and preserves the unsent draft in every state. |
| Manual-only takeover | The real broker refuses an unknown operation. Take over this operation is reachable; agent approval is unavailable. Actual takeover revokes the grant and releases the retained boundary. |
| Paused requests | Two page-script POSTs to the ungranted second local site are refused, including the retry after automatic grant revocation. Page requests paused and Take over render. Explicit authorized takeover releases the restriction. |

Changing the displayed charge to 13.50 rejects the original document-bound action before any effect. Deny correctly cancels the original canonical task. The fixture then starts a fresh task through the actual UI and explicitly re-hands the tab to it. A fresh 13.50 EUR UI decision performs exactly one local purchase effect. Manual/network stages retain that fresh live task; cancelled-task authority is never fabricated.

The 200% renderer uses 720×500 CSS pixels/DPR2, equivalent to the same 1440×1000 physical display as 100%/DPR1. Renderer contexts copy only the same managed-session cookies. Theme selection uses actual Tab, ArrowRight and Space. Layout shortcuts start outside remote inputs. Fields use ordinary scroll-to-view, not injected UI/CSS. Focus geometry includes computed outline width/offset, viewport and every clipping ancestor; the first Browser approval focus additionally asserts the actual inset offset is −3px, catching lazy stylesheet precedence regressions before the full matrix. Review/dialog controls use actual Tab/Enter, with modal-hidden readiness before exact focus-return assertions. Background draft bytes are inspected through the underlying textarea while the modal correctly excludes that background from the accessibility tree.

Original Cq30 visual evidence is preserved as [partial Chat 200% reading](browser-transactions-rendered-Cq30.md). Subsequent diagnostic runs exposed clipped dialog/footer/Browser outer focus rings; the final scoped inset rules and Browser selector specificity correct them. Historical bundle identities and harness corrections are recorded separately in the JSON. They are not retrospectively promoted to this final qualification.

Representative visually inspected 200% originals:

- [Browser exact reference and actions](screenshots/browser-transactions-BIq/exact-browser-approval-light-200.png)
- [Chat exact evidence and actions](screenshots/browser-transactions-BIq/exact-chat-approval-light-200.png)
- [Manual-only takeover](screenshots/browser-transactions-BIq/manual-only-takeover-light-200.png)
- [Retained request pause](screenshots/browser-transactions-BIq/network-request-paused-light-200.png)

The long card need not fit all fields at once; full reading is checked individually at real scroll positions. Axe incomplete contrast findings remain recorded and are not certification. One Strawberry/Starlette GraphQL request-body `ClientDisconnect` traceback was observed; its exact navigation/close phase was not instrumented, so no server-exception-free claim is made.

The source ancestor is `44320592c6880721f82d6369b1942a031f8ec825`; this proof includes the subsequent modal/footer/Browser focus follow-up whose exact hashes are recorded, without claiming it was already committed. HTML SHA256 is `a0d3ba83441b00f1b197368dff9a5cbcb4219de538d67caadc629fbb9726bb99`; entry SHA256 is `a68afcffa99e31c53e4e86e80049c966cf7cea7c2ca3b07e1ef39bfccd2fcab2`. No product source or build changed during the final run. The [source/Chromium boundary report](browser-transaction-boundary.md) retains recognized-challenge and arbitrary-JavaScript/passive-channel limits. Page labels are untrusted evidence, not merchant authenticity. Physical input/OS popups, installed native permissions and entitled live reviewer/media qualification remain separate. This fixture makes zero paid/provider queries or external transactions.

Reproduce with the explicit expected bundle:

```sh
PLAYWRIGHT_BROWSERS_PATH=/tmp/termx-managed-chromium TERMX_TRANSACTION_UI_BUNDLE=index-BIqFMukh.js .venv/bin/python tests/workspace_browser_transactions_e2e.py OUTPUT_DIR
```

Fixture: [workspace_browser_transactions_e2e.py](../../../tests/workspace_browser_transactions_e2e.py). Original final output: `/tmp/termx-browser-transactions-BIq-final`. Screenshot copies occupy about 4.1 MiB; historical diagnostics remain separate.
