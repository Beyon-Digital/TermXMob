# Host access implementation contract

Authentication adapters resolve stable issuer/subject identities. They never
supply application roles. `AuthorizationService` intersects current canonical
principal scopes, host role, expiring project grant, and immutable private
resource ownership. The host ID and all ownership records come from the host
SQLite database, not client claims. Revoking a grant increments policy revision;
existing control/event sockets close even when their scope list is unchanged.

Owner and admin can manage host resources. Operators/viewers receive explicit
project access and only their own conversation/task/terminal/browser resources.
Unclaimed legacy resources stay administrator-only for managed users. Existing
passcode/paired devices preserve their original scopes until the bounded legacy
migration deadline; administrators can end migration immediately.

Execution runs as the host OS user. The declared boundary is
`trusted-shared-machine`; the access endpoint explicitly returns
`tenant_isolation: false`. Operators cannot launch PTYs/agents unless an
administrator explicitly marks their host execution trusted. That permission
does **not** provide hostile-tenant isolation. Such isolation requires an
independent enforcing OS/container/VM runtime and associated proof.

## Managed administrative API

All administrative operations require a live managed administrator session;
cookie mutations also require same-origin CSRF. Remote transport requires TLS.
Bodies reject unknown fields. Secrets are excluded from inventories and audit.

| Path | Behavior |
| --- | --- |
| `GET /auth/access` | Current role, project grants, policy version, honest runtime boundary |
| `GET/POST /auth/admin/principals` | Inventory/create named local or externally mapped accounts |
| `PUT /auth/admin/principals/{id}/role` | Admin/operator/viewer preset, explicit trusted execution |
| `DELETE /auth/admin/principals/{id}` | Disable user and revoke sessions; owner cannot be disabled |
| `POST /auth/admin/principals/{id}/bindings` | Explicit issuer/subject mapping, no email merging/rebinding |
| `PUT/DELETE /auth/admin/principals/{id}/projects/{project}` | Expiring per-project action grants/revocation |
| `GET/DELETE /auth/admin/principals/{id}/sessions` | Inventory/revoke all device sessions |
| `POST /auth/admin/recovery-code` | Owner provisions one-use recovery credential; plaintext returned once |
| `POST /auth/recovery` | Local same-origin redemption rotates owner password, revokes owner sessions and legacy migration, restores explicit local entry |
| `POST /auth/admin/migration/end` | Immediately ends legacy paired-token access |
| `GET /auth/admin/adapters` | Versions, enabled state, health and checked timestamp |
| `POST /auth/admin/adapters/{id}/health` | Discovery/trust availability check; explicitly not a login proof |
| `PUT /auth/admin/adapters/{id}` | Enable/disable with explicit `revoke`/`expire` session policy; cannot disable last method |
| `GET /auth/admin/adapters/configuration` | Current public provider trust configuration and activation version |
| `POST /auth/admin/adapters/configuration/begin` | Staged OIDC PKCE begin; callback tests identity/policy without replacing administrator session |
| `POST /auth/admin/adapters/configuration/test` | Verify signed assertion or callback evidence against proposed config, current principal and policy |
| `PUT /auth/admin/adapters/configuration` | Atomically activate only after current test proofs; persist version/history and explicit session migration |
| `GET /auth/admin/audit` | Bounded metadata audit inventory |

Provider configuration uses the existing version-1 `adapters` and `bindings`
format. Supported built-in kinds are `oidc` (public Authorization Code + PKCE,
RS256 pinned discovery/JWKS) and `signed-assertion` (pinned Ed25519 trust with
five-minute maximum assertion lifetime and one-use IDs). Local-password remains
a separately enabled method. No regular plugin/agent can install authentication
code. Runtime configuration takes precedence over an old startup configuration
file; malformed trust settings fail closed.

Tests use controlled HTTP provider responses with actual RSA/Ed25519 signatures,
PKCE checks and browser cookies. A deployed external enterprise IdP/account and
cross-platform native secure storage remain separate platform integration proofs.

Native desktop authentication uses the Rust `workspace_login`/`workspace_request` bridge, keeps access credentials only in process memory and saves rotating refresh credentials only in the OS credential store. HTTP-only access plus CSRF cookies authenticate browser control transports. No long-lived JWT is placed in a native URL. `workspace_binary` handles constrained same-origin binary transfer; `workspace_detach` shares the coordinated session and restores/clamps window placement.

A native SSO adapter may register an exact `http://127.0.0.1:<host-port>/auth/oidc/<adapter>/callback` URI for its public PKCE client. All issuer/discovery/authorize/token/JWKS provider endpoints remain HTTPS. Other insecure redirects, hostname aliases and wrong callback paths are rejected. Register a separate HTTPS adapter where external-browser access uses a canonical HTTPS callback. `workspace_resume_sso` reads a completed callback's HttpOnly refresh cookie only in Rust, rotates it under the shared session lock, moves it into the OS credential store and removes the webview refresh cookie. Platform secure-store and signed-installer behavior still require release validation.

`tests/test_oidc_tls_interoperability.py` starts an actual ephemeral TLS HTTP IdP with a localhost SAN certificate and explicitly trusted test CA. It verifies discovery, authorize, PKCE code exchange, signed JWKS/nonce/audience/issuer validation, canonical principal mapping, staged policy proof and activation, managed cookie session, viewer restrictions and one-use callback rejection for both HTTPS and native loopback callbacks. It uses real HTTP/TLS sockets, no transport mock or disabled certificate verification.
