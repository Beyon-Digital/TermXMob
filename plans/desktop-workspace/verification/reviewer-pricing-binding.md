# Reviewer pricing and account binding verification

The host now accepts an explicit administrator pricing snapshot when evaluating a named configured provider/model. Currency, input/cached-input/output rates per million tokens, source, effective date and snapshot version are frozen with a distinct immutable qualification report. There are no default market rates, price lookups or automatic billing-account fallback.

`ResponsesReviewer` counts provider-reported input, cached-input and output usage. The report uses exact Decimal arithmetic to produce an **estimated** cost, never a billed charge. Missing or invalid request usage keeps cost unreported. Missing cached-input usage with different cache rates produces conservative estimated bounds; identical rates can still yield a complete estimate. Taxes, fees, discounts and billed charges are unknown. Safety qualification remains independent of pricing, while `qualification_evidence_complete` remains false without complete cost evidence.

Reviewer qualification binds to the current provider endpoint/model and credential through a private salted digest. Public reports contain only an opaque account revision. Credential changes under the same provider ID invalidate activation and active model review. The broker checks binding before classification, after its response and before releasing a cached internal/native execution permit. Revoked unconsumed model permits become an exact pending human decision; current host/grant checks still apply. Version names cannot make model decisions impersonate host rules.

Verification:

- `tests/test_reviewer_pricing_binding.py`, `test_auto_review.py`, `test_agent_action_review.py`, and `test_native_action_review.py`: **44 passed in 9.71 s**. Tests use managed authentication, actual evaluate/activate/export routes and actual bounded Responses parsing with `httpx.MockTransport`. Token values and rates are explicitly synthetic fixtures, not live billing measurements.
- Safety/browser frontend tests: **9 passed**, with TypeScript and production build passing. They cover required explicit pricing inputs, submitted snapshot values, stale-account activation refusal, usage and estimated-versus-billed wording.
- `tests/workspace_browser_accessibility_e2e.py`, with `TERMX_A11Y_SAFETY_ONLY=1`: **6 actual loaded states** (four Safety pricing forms and two sign-in screens), **0 axe violations**, **48 reachable/visible/uncovered keyboard checks**, minimum measured text contrast **5.16:1**, both themes at 100%/200% with reduced motion. **0 provider turns, 0 paid queries, 0 JavaScript errors**. The form was inspected without invoking evaluation.
- Actual managed Chromium renderer-crash test passed in **9.08 s**. It waits up to 15 seconds for the real crash event, replacing the previous one-second timing assumption. Terminal crash state, old-grant revocation, fresh human-only recovery and rejection of the old grant remain asserted.

Machine-readable evidence is in `reviewer-pricing-binding.json` and `safety-pricing-accessibility.json`. The original complete browser/manager accessibility proof remains separate.

**REVIEW-07 is not live-qualified.** No live review billing account/model was selected and no live provider query was made. Completing that acceptance requirement still requires an explicitly selected account/model and administrator pricing snapshot, then a real frozen qualification run reporting provider usage, latency, escalation, false allows and estimated cost. The code and fixture results do not stand in for that evidence.
