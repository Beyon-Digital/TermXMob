# Security model — engine extensions

## Trust boundaries

1. **Extension files** (`~/.agents/**`, project `.agents`, compat locations): untrusted content until host-root trust; never executed/sourced/templated during discovery; YAML safe-load only.
2. **Engine subprocesses**: spawned via `engines/env.py` resolution (explicit config → PATH → known dirs); no PTY for machine protocols; env constructed deliberately — never inherits whole host env into engines/MCP servers.
3. **MCP servers**: stdio spawn = code execution → requires `trust=trusted` in connection def. HTTP endpoints: SSRF-checked (scheme https/http, reject link-local `169.254.0.0/16`, metadata IPs, non-public ranges unless `lan:true`; DNS revalidated at connect).
4. **OAuth**: secrets only in `CredentialStore`; state bound to {user,device,host,connection,issuer,redirect}; PKCE S256; issuer-bound registrations never reused across issuers; nothing credential-bearing in events/logs/telemetry/markdown exports.
5. **Remote clients**: `localhost` on a phone ≠ host → "complete sign-in on host" UX; gateway endpoints session-scoped capability tokens, loopback or unix only.

## Enforcement honesty (matrix input)

| Engine | Tool filtering | Approvals | FS boundary | Notes |
|---|---|---|---|---|
| internal | native (registry+policy) | native (approvals table) | sandbox runner | existing guarantees kept |
| codex | advisory (engine owns tools) | native (app-server approval requests mediated) | codex sandbox flags + OS | TermX toolset narrows what's *offered* via config; bypass possible via engine's own shell → disclosed |
| devin/grok (ACP) | advisory | native (`session/request_permission` → policy engine) | `fs/*` callbacks scoped to task cwd; terminal caps gated | negotiated caps only |
| claude | gateway (SDK `can_use_tool` + hooks) | gateway+native permission modes | `cwd` + `disallowed_tools` + hooks | hooks give real deny path |

A profile marked strict must not launch under an engine whose enforcement is advisory-only unless the user explicitly accepts "advisory" — surfaced in UI as authority disclosure, not a hidden downgrade.

## Things this design refuses

- No second agent loop around external engines (TermX supervises; engine loops stay authoritative).
- No native session file surgery to fake resume — bindings store engine-reported ids only.
- No credential values in catalogs, events, exports, logs, or shareable `~/.agents` files.
- No auto-spawn/install/OAuth on discovery alone.
- No `terminal`/`fs` ACP capability advertisement while returning stub successes.
- Skill `allowed-tools`/dependency declarations request but cannot grant access.
