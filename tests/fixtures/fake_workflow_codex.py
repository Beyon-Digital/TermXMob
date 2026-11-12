"""Local app-server protocol fixture. Never invokes inference or host tools."""
import json
import os
import sys


def send(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    params = message.get("params") or {}
    identifier = message.get("id")
    if method == "initialize":
        assert params["capabilities"]["experimentalApi"] is True
        send({"id": identifier, "result": {"userAgent": "workflow-fixture"}})
    elif method == "initialized":
        pass
    elif method == "thread/start":
        tools = params["dynamicTools"]
        assert {"get_workflow", "save_workflow"}.issubset({tool["name"] for tool in tools})
        assert all(tool["type"] == "function" and tool["inputSchema"]["additionalProperties"] is False for tool in tools)
        send({"id": identifier, "result": {"thread": {"id": "native"}}})
    elif method == "turn/start":
        send({"id": identifier, "result": {"turn": {"id": "turn"}}})
        send({"method": "turn/started", "params": {"threadId": "native", "turn": {"id": "turn"}}})
        save = os.environ.get("WORKFLOW_FIXTURE_SAVE") == "1"
        send({"id": 900, "method": "item/tool/call", "params": {"threadId": "native", "turnId": "turn",
            "callId": "wire-call", "tool": "save_workflow" if save else "get_workflow",
            "arguments": {"name": "Exact", "source": "return args;"} if save else {"workflow_id": "saved"}}})
    elif method == "fixture/exit":
        break
    elif identifier == 900:
        result = message["result"]
        assert result["success"] is True
        assert result["contentItems"][0]["type"] == "inputText"
        assert json.loads(result["contentItems"][0]["text"])["workflow"]["id"] == "saved"
        send({"method": "turn/completed", "params": {"threadId": "native", "turn": {"id": "turn", "status": "completed"}}})
    elif identifier is not None:
        send({"id": identifier, "result": {}})
