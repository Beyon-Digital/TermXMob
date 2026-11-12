"""Claude's in-process workflow MCP server; no tokens or host paths exported."""
from __future__ import annotations

import json
import secrets


def controlled_server(host, engine, binding, task_id, schemas):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    definitions = []
    for schema in schemas:
        def handler(name):
            async def invoke(args):
                try:
                    if binding.extensions_snapshot.get("managed_task_id") != task_id or binding.status in {"closed", "lost"}:
                        raise PermissionError("Native task changed")
                    result = await host.native_execute(engine, binding, name, args,
                        "workflow_" + secrets.token_hex(16))
                except Exception:
                    result = {"ok": False, "refused": True, "error": {"code": "workflow_refused",
                        "message": "Workflow tool authority or transport is unavailable."}}
                # claude-agent-sdk 0.2.160 reads is_error (not isError) from
                # SDK handler dictionaries before producing MCP CallToolResult.
                return {"is_error": result.get("ok") is False,
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            return invoke
        definitions.append(tool(schema["name"], schema["description"], schema["parameters"])(handler(schema["name"])))
    return create_sdk_mcp_server(name="termx-workflows", version="1.0.0", tools=definitions)
