#!/usr/bin/env python3
"""Deterministic fake `codex app-server` for fixture tests.

Speaks newline-delimited JSON-RPC (no `jsonrpc` member) on stdio, exactly like
`codex app-server`. Behaviour knobs via env:

- FAKE_CODEX_AUTH: "apikey" (authenticated), "" (signed out), "required"
- FAKE_CODEX_APPROVE_WHEN: substring — when turn input contains it, the server
  issues an `item/commandExecution/requestApproval` server→client request and
  only completes the turn after the client responds.
- FAKE_CODEX_HANG: "1" — never completes turns (for interrupt tests).
- FAKE_CODEX_DIE_AFTER_INIT: "1" — exits after `initialized`.
"""

from __future__ import annotations

import json
import os
import sys
import time

AUTH = os.environ.get("FAKE_CODEX_AUTH", "apikey")
APPROVE_WHEN = os.environ.get("FAKE_CODEX_APPROVE_WHEN", "")
HANG = os.environ.get("FAKE_CODEX_HANG", "") == "1"
DIE = os.environ.get("FAKE_CODEX_DIE_AFTER_INIT", "") == "1"

seq = 0
pending_approval: dict[str, object] = {}
threads: dict[str, dict] = {}
pending_turn: dict[str, object] = {}


def send(msg: dict) -> None:
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


def finish_turn(thread_id: str, turn_id: str, status: str = "completed", error=None):
    notify("turn/completed", {
        "turn": {"id": turn_id, "threadId": thread_id, "status": status,
                 "error": error},
    })


def start_turn(thread_id: str, text: str) -> str:
    global pending_turn
    turn_id = f"turn_{seq}"
    pending_turn = {"thread_id": thread_id, "turn_id": turn_id}
    notify("turn/started", {"turn": {"id": turn_id, "threadId": thread_id,
                                    "status": "inProgress", "items": []}})
    if APPROVE_WHEN and APPROVE_WHEN in text:
        item_id = f"item_{seq}"
        notify("item/started", {"item": {"id": item_id, "type": "commandExecution",
                                        "command": ["echo", "hi"], "status": "inProgress"},
                               "threadId": thread_id})
        req_id = f"req_{seq}"
        pending_approval["id"] = req_id
        pending_approval["thread_id"] = thread_id
        send({
            "method": "item/commandExecution/requestApproval",
            "id": req_id,
            "params": {"threadId": thread_id, "turnId": turn_id,
                       "itemId": item_id, "command": ["echo", "hi"]},
        })
        return turn_id
    if HANG:
        return turn_id
    complete_normally(thread_id, turn_id, text)
    return turn_id


def complete_normally(thread_id: str, turn_id: str, text: str) -> None:
    item_id = f"item_{seq}"
    notify("item/started", {"item": {"id": item_id, "type": "agentMessage",
                                    "text": ""}, "threadId": thread_id})
    reply_text = f"fake says: {text[:60]}"
    for chunk in (reply_text[:20], reply_text[20:]):
        notify("item/agentMessage/delta",
               {"itemId": item_id, "threadId": thread_id, "delta": chunk})
    notify("item/completed", {"item": {"id": item_id, "type": "agentMessage",
                                       "text": reply_text},
                              "threadId": thread_id})
    finish_turn(thread_id, turn_id)


def handle(msg: dict) -> None:
    global seq
    seq += 1
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    # response to a server->client request
    if method is None and req_id is not None:
        if pending_approval.get("id") == req_id:
            decision = (msg.get("result") or {}).get("decision")
            notify("serverRequest/resolved", {"threadId": pending_approval["thread_id"],
                                              "requestId": req_id})
            if decision in ("accept", "acceptForSession"):
                notify("item/completed", {"item": {"id": f"item_{seq}",
                                                   "type": "commandExecution",
                                                   "status": "completed",
                                                   "exitCode": 0},
                                          "threadId": pending_approval["thread_id"]})
                complete_normally(pending_approval["thread_id"],
                                  pending_turn["turn_id"], "(after approval)")
            else:
                notify("item/completed", {"item": {"id": f"item_{seq}",
                                                   "type": "commandExecution",
                                                   "status": "declined"},
                                          "threadId": pending_approval["thread_id"]})
                finish_turn(pending_approval["thread_id"],
                            pending_turn["turn_id"], "completed")
            pending_approval.clear()
        return

    if method == "initialize":
        reply(req_id, {"userAgent": "fake-codex/1.0", "platformFamily": "unix",
                       "platformOs": sys.platform})
        return
    if method == "initialized":
        if DIE:
            sys.exit(3)
        return
    if method == "account/read":
        if AUTH == "":
            reply(req_id, {"account": None, "requiresOpenaiAuth": False})
        elif AUTH == "required":
            reply(req_id, {"account": None, "requiresOpenaiAuth": True})
        else:
            reply(req_id, {"account": {"type": AUTH, "email": None,
                                       "planType": "pro"},
                           "requiresOpenaiAuth": True})
        return
    if method == "model/list":
        reply(req_id, {"data": [
            {"id": "gpt-fake-1", "model": "gpt-fake-1", "displayName": "Fake 1",
             "isDefault": True},
            {"id": "gpt-fake-2", "model": "gpt-fake-2", "displayName": "Fake 2"},
        ], "nextCursor": None})
        return
    if method == "thread/start":
        thread_id = f"thr_{seq}"
        threads[thread_id] = {"id": thread_id}
        reply(req_id, {"thread": {"id": thread_id, "sessionId": thread_id,
                                  "preview": "", "ephemeral": False,
                                  "modelProvider": "openai",
                                  "createdAt": int(time.time())},
                       "instructionSources": []})
        notify("thread/started", {"thread": {"id": thread_id}})
        return
    if method == "thread/resume":
        tid = params.get("threadId")
        if tid in threads:
            reply(req_id, {"thread": {"id": tid}, "instructionSources": []})
        else:
            reply(req_id, error={"code": -32000, "message": "thread not found"})
        return
    if method == "thread/list":
        reply(req_id, {"data": [{"id": t} for t in threads]})
        return
    if method == "turn/start":
        thread_id = params["threadId"]
        text = " ".join(
            i.get("text", "") for i in params.get("input", [])
            if i.get("type") == "text"
        )
        turn_id = start_turn(thread_id, text)
        reply(req_id, {"turn": {"id": turn_id, "status": "inProgress",
                                "items": [], "error": None}})
        return
    if method == "turn/steer":
        if params.get("expectedTurnId") == pending_turn.get("turn_id"):
            reply(req_id, {"turnId": pending_turn["turn_id"]})
        else:
            reply(req_id, error={"code": -32000, "message": "no active turn"})
        return
    if method == "turn/interrupt":
        finish_turn(params["threadId"], params["turnId"], "interrupted")
        pending_turn.clear()
        reply(req_id, {})
        return
    if req_id is not None:
        reply(req_id, error={"code": -32601, "message": f"unknown {method}"})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as exc:  # keep the pipe alive
            sys.stderr.write(f"fake-codex error: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
