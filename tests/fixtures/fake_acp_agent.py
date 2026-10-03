#!/usr/bin/env python3
"""Deterministic fake ACP agent (full JSON-RPC 2.0 on stdio).

Env knobs:
- FAKE_ACP_APPROVE: substring — matching prompts trigger a
  session/request_permission client call; turn completes after the reply.
- FAKE_ACP_AUTH_REQUIRED: "1" — initialize reports authMethods.
- FAKE_ACP_HANG: "1" — prompt never resolves until session/cancel.
"""

from __future__ import annotations

import json
import os
import sys

APPROVE_WHEN = os.environ.get("FAKE_ACP_APPROVE", "")
AUTH_REQUIRED = os.environ.get("FAKE_ACP_AUTH_REQUIRED", "") == "1"
HANG = os.environ.get("FAKE_ACP_HANG", "") == "1"

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


def handle(msg: dict) -> None:
    global seq
    seq += 1
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method is None and req_id is not None:
        # response to our session/request_permission
        if pending.get("id") == req_id:
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
            "protocolVersion": params.get("protocolVersion", 1),
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
        reply(req_id, {"sessionId": sid})
        return
    if method == "session/load":
        sid = params.get("sessionId")
        if sid in sessions:
            reply(req_id, {})
        else:
            reply(req_id, error={"code": -32000, "message": "no such session"})
        return
    if method == "session/list":
        reply(req_id, {"sessions": [{"sessionId": s} for s in sessions]})
        return
    if method == "session/prompt":
        sid = params.get("sessionId")
        text = "".join(
            b.get("text", "") for b in params.get("prompt", [])
            if isinstance(b, dict) and b.get("type") == "text")
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
