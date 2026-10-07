# Managed host authentication

This is an initial backend contract for section 9 of the approved workspace
plan. It is not the finished multiuser authorization or desktop UI rollout.

`AppState.identity` composes the identity directory, persistent session service
and configured `AuthenticationPort` adapters. `Auth` remains the compatibility
facade for existing HTTP/GraphQL/PTY/desktop/LSP host services. The session DB is
`TERMX_CONFIG_DIR/identity.sqlite3`, created with mode 0600. Host signing keys,
identity mappings and session state stay on the host; refresh credentials and
CSRF secrets are persisted as hashes. OS secure-storage integration belongs to
the native client migration.

| Endpoint | Contract |
| --- | --- |
| `GET /auth/methods` | Safe method labels, flow types and configuration status |
| `POST /auth/setup` | First owner; loopback + exact Origin + current bootstrap passcode if set; password 12–1024 characters |
| `POST /auth/login` | Local-password or configured custom assertion; rate-limited; no unknown-user disclosure |
| `POST /auth/oidc/{method}/begin` | Same-origin start of a configured OIDC flow; returns authorization URL and sets browser-binding cookie |
| `GET /auth/oidc/{method}/callback` | One-time bound state/code exchange; sets the same managed session cookies and redirects to `/` |
| `POST /auth/refresh` | Rotate current refresh family; cookie flow requires Origin/CSRF; native body supplies `refresh_token` |
| `GET /auth/me` | Current principal, session ID, expiration and target host ID |
| `GET /auth/sessions` | Current principal's device sessions; no secrets |
| `DELETE /auth/sessions/{id}` | Revoke only a session owned by the current principal |
| `POST /auth/logout` | Revoke this session and clear access, refresh and CSRF cookies |

Setup/login body fields: `method` (default `local-password`), `username`,
`password`, `assertion` (custom only), `device_name`, `transport` (`cookie` or
`bearer`). Cookie responses return `session_id`, `expires_in` and `csrf_token`;
access/refresh credentials remain HttpOnly. Bearer responses include access and
refresh credentials; native callers must store the refresh credential in OS
secure storage and keep access credentials in memory.

Browser mutations, including GraphQL POST, require the exact `Origin` plus
`X-Termx-CSRF`. Access cookies apply to `/`; refresh cookies apply only to `/auth`.
The `termx_csrf` cookie is readable so reloads and detached windows can restore
the anti-CSRF header; this value cannot authenticate an API call by itself.
An expired access cookie can still refresh using the refresh cookie and current
CSRF token. OIDC binding cookies are Secure/HttpOnly/SameSite=Lax to permit the
IdP's top-level GET callback; other session cookies are SameSite=Strict.

Coordinate refresh with a shared single-flight mechanism across windows.
Replaying a consumed refresh credential revokes its entire session; the backend
never issues a second independently valid branch. Retry a lost response only
through the planned coordinated client protocol, not by blindly replaying the
old token. Automatic task execution uses separate delegated grants later.

After owner activation, existing device tokens work for seven days with the
same existing scopes. Passcode API login stops immediately. Re-pairing requires
managed host-admin authority and cannot exceed the migration deadline. Managed
access JWTs belong in an Authorization header or cookie, never a URL. An open
unconfigured host no longer permits remote protected operations. LAN and tunnel
clients require TLS; proxy deployments must preserve the trusted scheme/peer.

## Trusted adapter configuration

Set `TERMX_AUTH_ADAPTERS_FILE` to an owner-readable JSON file (0600 on Unix).
This startup configuration is outside the ordinary agent/plugin registry.
Invalid adapter IDs, unsupported kinds, unknown principals or conflicting
identity bindings abort startup. No client can choose arbitrary issuers or keys.

```json
{
  "version": 1,
  "adapters": [
    {
      "id": "company",
      "kind": "oidc",
      "label": "Company SSO",
      "issuer": "https://identity.example.com",
      "client_id": "configured-public-pkce-client",
      "redirect_uri": "https://termx.example.com/auth/oidc/company/callback"
    }
  ],
  "bindings": [
    {
      "issuer": "https://identity.example.com",
      "subject": "immutable-provider-subject",
      "principal_id": "existing-termx-principal-id"
    }
  ]
}
```

Register the exact callback with a real OIDC provider supporting Authorization
Code + PKCE S256 and RS256 ID tokens. The initial adapter uses a public PKCE
client; confidential-client authentication is not yet implemented. Map stable
issuer/subject explicitly to an existing principal after local setup. An ID token
is exchanged inside this flow and cannot authenticate arbitrary TermX APIs.

A `signed-assertion` configuration requires `id`, `kind`, `label`, `issuer`,
`audience` and a PEM `public_key`. The configured issuer signs Ed25519 assertions
with `typ=termx-identity+jwt` and required `iss/aud/sub/iat/exp/jti` claims. Lifetime
must be at most five minutes; assertion IDs are consumed once. This is a custom
reference integration, not support for arbitrary enterprise bearer tokens.

The adapter registry is also injectable for host integrations/tests through
`AuthenticationService.register_adapter()` and `AppState(identity=...)`.
Provider identity evidence is separate from authorization scopes. Do not grant
new users merely because their IdP email matches an existing local account.
