#!/usr/bin/env python3
"""Deterministic fake ACP agent (full JSON-RPC 2.0 on stdio).

Env knobs:
- FAKE_ACP_APPROVE: substring — matching prompts trigger a
  session/request_permission client call; turn completes after the reply.
- FAKE_ACP_AUTH_REQUIRED: "1" — initialize reports authMethods.
- FAKE_ACP_HANG: "1" — prompt never resolves until session/cancel.
- FAKE_ACP_NO_MODE: "1" — omit native mode selectors (including legacy modes).
- FAKE_ACP_DEFAULT_MODE: native mode used by a new session (default "ask").
"""

from __future__ import annotations

import json
import os
import sys

APPROVE_WHEN = os.environ.get("FAKE_ACP_APPROVE", "")
AUTH_REQUIRED = os.environ.get("FAKE_ACP_AUTH_REQUIRED", "") == "1"
HANG = os.environ.get("FAKE_ACP_HANG", "") == "1"
LEGACY = os.environ.get("FAKE_ACP_LEGACY", "") == "1"
NO_MODE = os.environ.get("FAKE_ACP_NO_MODE", "") == "1"
DEFAULT_MODE = os.environ.get("FAKE_ACP_DEFAULT_MODE", "ask")
CALLBACKS = os.environ.get("FAKE_ACP_CALLBACKS", "") == "1"
LOG_FILE = os.environ.get("FAKE_ACP_LOG", "")

seq = 0
sessions: dict[str, dict] = {}
pending: dict[str, object] = {}

STATE_FILE = os.environ.get("FAKE_ACP_STATE_FILE", "")


def _load_state() -> None:
    if not STATE_FILE:
        return
    try:
        sessions.update(json.loads(open(STATE_FILE).read()))
    except (OSError, ValueError):
        pass


def _save_state() -> None:
    if not STATE_FILE:
        return
    try:
        open(STATE_FILE, "w").write(json.dumps(sessions))
    except OSError:
        pass


def send(msg: dict) -> None:
    msg.setdefault("jsonrpc", "2.0")
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result=None, error=None):
    msg = {"id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result if result is not None else {}
    send(msg)


def notify(method: str, params: dict) -> None:
    send({"method": method, "params": params})


def chunk(sid: str, text: str) -> None:
    notify("session/update", {"sessionId": sid, "update": {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text}}})


def complete(sid: str, text: str) -> None:
    for piece in (text[:15], text[15:]):
        if piece:
            chunk(sid, piece)


def configuration(sid: str) -> dict:
    values = sessions[sid]
    modes = {"currentModeId": values.get("mode", DEFAULT_MODE), "availableModes": [
        {"id": "ask", "name": "Ask"}, {"id": "code", "name": "Code"}]}
    if LEGACY:
        return {} if NO_MODE else {"modes": modes}
    result = {"modes": modes, "configOptions": [
        {"id": "mode", "name": "Mode", "category": "mode", "type": "select",
         "currentValue": values.get("mode", DEFAULT_MODE), "options": [
             {"value": "ask", "name": "Ask"}, {"value": "code", "name": "Code"}]},
        {"id": "model", "name": "Model", "category": "model", "type": "select",
         "currentValue": values.get("model", "slow"), "options": [
             {"group": "models", "name": "Models", "options": [
                 {"value": "slow", "name": "Slow"}, {"value": "fast", "name": "Fast"}]}]},
        {"id": "reasoning", "name": "Reasoning", "category": "thought_level", "type": "select",
         "currentValue": values.get("reasoning", "low"), "options": [
             {"value": "low", "name": "Low"}, *(
                 [{"value": "high", "name": "High"}] if values.get("model", "slow") == "slow" else [])]},
        {"id": "safe", "name": "Safe", "type": "boolean", "currentValue": values.get("safe", True)},
    ]}
    if NO_MODE:
        result.pop("modes")
        result["configOptions"] = [o for o in result["configOptions"] if o.get("category") != "mode"]
    return result


def next_callback():
    sid = pending["session_id"]
    callbacks = pending["callbacks"]
    if not callbacks:
        complete(sid, "callbacks done")
        reply(pending["prompt_id"], {"stopReason": "end_turn"})
        pending.clear()
        return
    method, params = callbacks.pop(0)
    if method != "terminal/create":
        params["terminalId"] = pending["terminal_id"]
    req = seq * 1000
    pending["id"] = req
    send({"method": method, "id": req, "params": {"sessionId": sid, **params}})


def handle(msg: dict) -> None:
    global seq
    seq += 1
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if LOG_FILE:
        with open(LOG_FILE, "a") as log:
            log.write(json.dumps(msg) + "\n")

    if method is None and req_id is not None:
        # response to our session/request_permission
        if pending.get("id") == req_id:
            if "callbacks" in pending:
                if "error" in msg:
                    reply(pending["prompt_id"], error=msg["error"])
                    pending.clear()
                    return
                result = msg.get("result") or {}
                if "terminalId" in result:
                    pending["terminal_id"] = result["terminalId"]
                next_callback()
                return
            sid = pending["session_id"]
            outcome = (msg.get("result") or {}).get("outcome") or {}
            if outcome.get("outcome") == "selected":
                complete(sid, "approved, done")
            else:
                chunk(sid, "denied")
            notify("session/update", {"sessionId": sid, "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc1", "status": "completed"}})
            # resolve the still-open prompt request
            reply(pending["prompt_id"], {"stopReason": "end_turn"})
            pending.clear()
        return

    if method == "initialize":
        result = {
            "protocolVersion": int(os.environ.get("FAKE_ACP_VERSION", params.get("protocolVersion", 1))),
            "agentCapabilities": {"loadSession": True},
            "agentInfo": {"name": "fake-acp", "version": "0.1"},
            "authMethods": ([{"id": "oauth", "name": "Sign in"}]
                            if AUTH_REQUIRED else []),
        }
        reply(req_id, result)
        return
    if method == "authenticate":
        reply(req_id, {})
        return
    if method == "session/new":
        sid = f"acp_{seq}"
        sessions[sid] = {"cwd": params.get("cwd")}
        _save_state()
        reply(req_id, {"sessionId": sid, **configuration(sid)})
        return
    if method == "session/load":
        sid = params.get("sessionId")
        if sid in sessions:
            reply(req_id, configuration(sid))
        else:
            reply(req_id, error={"code": -32000, "message": "no such session"})
        return
    if method == "session/set_config_option":
        sid = params["sessionId"]
        key = params["configId"]
        sessions[sid][key] = params["value"]
        _save_state()
        notify("session/update", {"sessionId": sid, "update": {
            "sessionUpdate": "config_option_update", "configOptions": configuration(sid)["configOptions"]}})
        reply(req_id, {"configOptions": configuration(sid)["configOptions"]})
        return
    if method == "session/set_mode":
        sid = params["sessionId"]
        sessions[sid]["mode"] = params["modeId"]
        _save_state()
        reply(req_id, {})
        return
    if method == "session/list":
        reply(req_id, {"sessions": [{"sessionId": s} for s in sessions]})
        return
    if method == "session/prompt":
        sid = params.get("sessionId")
        text = "".join(
            b.get("text", "") for b in params.get("prompt", [])
            if isinstance(b, dict) and b.get("type") == "text")
        if CALLBACKS:
            pending.update({"prompt_id": req_id, "session_id": sid, "callbacks": [
                ("terminal/create", {"command": sys.executable, "args": ["-c", "print('callback output')"]}),
                ("terminal/wait_for_exit", {}), ("terminal/output", {}), ("terminal/release", {}),
            ]})
            next_callback()
            return
        if HANG:
            pending["prompt_id"] = req_id
            pending["session_id"] = sid
            pending["cancel_only"] = True
            return
        if APPROVE_WHEN and APPROVE_WHEN in text:
            pending["prompt_id"] = req_id
            pending["session_id"] = sid
            req = seq * 1000
            pending["id"] = req
            send({
                "method": "session/request_permission",
                "id": req,
                "params": {
                    "sessionId": sid,
                    "toolCall": {"toolCallId": "tc1", "title": "Run tests",
                                 "kind": "execute"},
                    "options": [
                        {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
                    ],
                },
            })
            return
        complete(sid, f"fake-acp says: {text[:60]}")
        reply(req_id, {"stopReason": "end_turn",
                       "usage": {"used": 42, "size": 1000}})
        return
    if method == "session/cancel":
        if os.environ.get("FAKE_ACP_IGNORE_CANCEL") == "1":
            return
        pid = pending.pop("prompt_id", None)
        if pid is not None:
            reply(pid, {"stopReason": "cancelled"})
            pending.clear()
        return
    if req_id is not None:
        reply(req_id, error={"code": -32601, "message": f"unknown {method}"})


def main() -> None:
    _load_state()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as exc:
            sys.stderr.write(f"fake-acp error: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
