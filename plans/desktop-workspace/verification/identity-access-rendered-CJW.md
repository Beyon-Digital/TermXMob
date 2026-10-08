# Actual Access UI and host effects — CJW

The immutable production bundle `index-CJWjwsmg.js` passed the isolated [Access fixture](../../../tests/workspace_identity_access_e2e.py). All64 UI file hashes and eight theme/layout states are recorded in [the machine-readable report](identity-access-rendered-CJW.json). This proves its scoped behavior, not final installed-native or live-enterprise qualification.

The actual UI created an organization/group and granted access to only the selected project. A member could not access administrator routes or see group/audit controls. Removing membership returned401 for the existing device session. An exact configured issuer/claim mapping accepted a real pinned signed assertion; unmapped administrator text conferred no authority, and removing the mapping revoked that session.

A concurrent administrator edit preserved the dirty group draft, disabled stale save, and required explicit current-revision review before saving those exact bytes. Acknowledged audit limits removed an expired fixture record, recorded the retention operation, and cleared the acknowledgement. No real host audit, key or provider account was used.

Eight dark/light100/200-equivalent viewport states passed named-field Tab/ShiftTab and center-uncovered checks, with zero JavaScript errors, axe violations or page overflow. Incomplete axe findings remain in the report. Dark100 group and light200 retention screenshots were visually inspected. Stable Select accessible names were fixed after the first rendered attempt; fixture setup/event-loop errors were corrected before this completed proof.

This snapshot precedes the additional SessionPolicyManager. It does not qualify that subsequent interface.
