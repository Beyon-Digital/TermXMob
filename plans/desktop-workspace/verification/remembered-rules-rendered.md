# Remembered permissions: inspect, bounded edit and revoke

Production bundle `index-DaXRE1Tz.js` / `index-CwaImZbJ.css` was copied unchanged before host startup. `remembered-rules-rendered.json` records the entry hash, 14 loaded axe states and 50 actual Tab/focus checks across dark/light at 100%/200%, with reduced motion. No axe violations, prohibited-name incomplete findings, JavaScript errors or paid queries.

The user inspects the exact immutable scope, edits BLOCK→ALLOW and expiry through the real form, and revokes the same rule. The host checks the original live managed session, canonical running fixture task, policy revision, exact resource authority and rule revision. The stored scope remains byte-for-byte equal; revision increments once and expiry stays within the selected bound. Revoke removes the record. Native decision pickers use Playwright `select_option` after actual Tab/focus; all buttons, disclosures and text inputs use keyboard navigation. Screenshots are under `screenshots/remembered-rules-DaXR/`.

`tests/test_remembered_rule_edit.py` has ten meaningful host cases covering foreign owner, current session/task/policy, browser grant/origin/tool bounds, stale edit, extra scope fields, sensitive allow refusal, expiry and inherited deny precedence. UI form regressions are in `features/safety-rules.test.tsx`.

The initial rule is explicitly seeded against a real managed session and canonical running fixture task; there is no provider worker for this proof and no website effect. This qualifies owner rule-management behavior, not a live reviewer model or installed native application.
