# Scoped MCP connection and preset workflow

The immutable `index-Cfb7wns2.js` bundle passed the actual hosted workflow. The user created an untrusted stdio connection limited to an enrolled project and the exact `add` tool, stored a write-only managed environment credential, explicitly granted trust, connected, inspected the real schema and called `add(7, 5)` with result 12. No credential value appeared in page text or returned connection metadata.

The user created an Internal preset through the typed connection/tool selection controls. Its canonical binding selected `connection.ui-scoped` with `add` and deliberately included `mcp_catalog` and `mcp_call`. Returning to the retained conversation refreshed the preset inventory without reloading. Selecting that preset and explicitly sending a task went through ordinary human approvals and the actual AgentManager tool loop, producing `add(2, 3)` result 5 through the real stdio MCP server.

Switching to another canonical execution project made the connection unavailable and disabled reconnect. Editing its tool allowlist to an explicit empty list persisted deny-all. Four light/dark 100%/200% equivalent viewport states had zero axe violations, document overflow or JavaScript errors. Metadata, exact asset hashes and inspected screenshots are recorded in `mcp-manager-rendered.json`.

The inference provider was a deterministic no-network fixture. Source tests separately qualify current-session revocation, digest changes, credential rotation, stale pooled transports and project denial before secrets/effects. Direct project-scoped native adapters and host MCP in runner sessions remain explicitly refused; this proof qualifies the local reviewed Internal broker.
