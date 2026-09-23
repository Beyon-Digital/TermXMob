from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from termx.agent.computer import ComputerController
from termx.agent.context import workspace_manifest
from termx.agent.execution import run_shell
from termx.agent.policy import (
    PolicyDecision,
    evaluate_computer,
    evaluate_shell,
    is_mutating_shell,
    redact,
)
from termx.agent.providers import (
    OpenAIResponsesAdapter,
    ProviderAdapter,
    ProviderCall,
    ProviderError,
    ProviderTurn,
)
from termx.agent.secrets import CredentialStore
from termx.agent.store import ACTIVE_STATUSES, AgentStore, configured_models

AdapterFactory = Callable[[dict[str, Any], str], ProviderAdapter]
DEFAULT_LIMITS = {"max_steps": 24, "max_seconds": 900, "shell_timeout_s": 120}


class AgentManager:
    """Coordinates durable Agent runs, approvals, and live event replay."""

    def __init__(
        self,
        store: AgentStore,
        credentials: CredentialStore,
        desktop: Any,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.store = store
        self.credentials = credentials
        self.desktop = desktop
        self._adapter_factory = adapter_factory or self._default_adapter
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._steering: dict[str, list[str]] = defaultdict(list)
        self._pending_approval_calls: dict[str, dict[str, Any]] = {}
        self._computer = ComputerController()
        for task in self.store.list_tasks(limit=500):
            if task["status"] in ACTIVE_STATUSES:
                self.store.update_task(
                    task["id"],
                    status="failed",
                    error="The Termx host stopped before this task completed.",
                )
                self.store.append_event(
                    task["id"],
                    "task.failed",
                    {"message": "The Termx host stopped before this task completed."},
                )

    # Provider configuration -----------------------------------------

    def list_providers(self) -> list[dict[str, Any]]:
        return self.store.list_providers()

    def save_provider(
        self,
        *,
        provider_id: str,
        kind: str,
        name: str,
        base_url: str,
        model: str,
        capabilities: list[str],
        api_key: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"openai", "openai-compatible"}:
            raise ValueError("provider kind must be openai or openai-compatible")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("provider URL must use HTTP or HTTPS")
        allowed = [item for item in capabilities if item in {"shell", "functions", "computer"}]
        if not allowed:
            allowed = ["shell"]
        configured = bool(self.credentials.get(provider_id))
        if api_key:
            self.credentials.set(provider_id, api_key)
            configured = True
        provider = self.store.put_provider(
            provider_id,
            kind=kind,
            name=name,
            base_url=base_url,
            model=model,
            capabilities=allowed,
            secret_configured=configured,
        )
        return provider

    async def test_provider(self, provider_id: str) -> dict[str, str]:
        provider = self._provider(provider_id)
        message = await self._adapter(provider).test()
        return {"status": "connected", "message": message}

    def delete_provider(self, provider_id: str) -> bool:
        deleted = self.store.delete_provider(provider_id)
        if deleted:
            self.credentials.delete(provider_id)
        return deleted

    # Task lifecycle --------------------------------------------------

    async def create_task(
        self,
        *,
        prompt: str,
        cwd: str,
        provider_id: str,
        limits: dict[str, Any] | None = None,
        mode: str = "agent",
        model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("task prompt is required")
        mode = "ask" if mode == "ask" else "agent"
        root = Path(cwd).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project folder is not a directory")
        images = _decode_images(attachments)
        provider = dict(self._provider(provider_id))
        chosen = _resolve_model(provider, model)
        provider["model"] = chosen
        adapter = self._adapter(provider)
        bounded = self._limits(limits)
        task = self.store.create_task(
            prompt=prompt,
            cwd=str(root),
            provider_id=provider_id,
            model=chosen,
            limits=bounded,
            mode=mode,
        )
        task_id = task["id"]
        uploads = [
            self.store.save_artifact(task_id, "upload", mime, data)
            for _name, mime, data in images
        ]
        if uploads:
            self._emit(task_id, "user.media", {"artifacts": uploads})
        self._emit(task_id, "task.created", {"task": task})
        try:
            manifest = await asyncio.to_thread(workspace_manifest, str(root))
            upload_ids = [item["id"] for item in uploads]
            if mode == "ask":
                # Ask mode is read-only and low-risk, so it starts immediately
                # instead of waiting for plan approval.
                history = [self._upload_message(task_id, upload_ids)] if upload_ids else []
                runtime = {"manifest": manifest, "history": history, "uploads": upload_ids, "step": 0, "started_at": None}
                self.store.update_task(task_id, runtime=runtime)
                self._launch(task_id, self._drive(task_id))
                return self.store.get_task(task_id, include_events=True) or task
            plan, response_id = await adapter.plan(prompt, str(root), manifest)
            history = [self._upload_message(task_id, upload_ids)] if upload_ids else []
            runtime = {"manifest": manifest, "history": history, "uploads": upload_ids, "step": 0, "started_at": None}
            task = self.store.update_task(
                task_id,
                status="awaiting_approval",
                plan=plan,
                previous_response_id=response_id,
                runtime=runtime,
            )
            self._emit(task_id, "plan.ready", {"plan": plan})
            approval = self.store.create_approval(
                task_id,
                "plan",
                {"title": "Approve this plan", "plan": plan, "consequence": "Starts work on the paired host"},
            )
            self._emit(task_id, "approval.requested", {"approval": approval})
            self._emit(task_id, "task.status", {"status": "awaiting_approval"})
            return self.store.get_task(task_id, include_events=True) or task
        except Exception as exc:
            self._fail(task_id, exc)
            return self.store.get_task(task_id, include_events=True) or task

    async def resolve_approval(self, task_id: str, approval_id: str, decision: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] != "awaiting_approval":
            raise ValueError("task is not awaiting approval")
        approval = self.store.get_approval(approval_id)
        if approval is None or approval["task_id"] != task_id:
            raise KeyError(approval_id)
        if (
            decision != "denied"
            and approval["kind"] == "tool"
            and approval_id not in self._pending_approval_calls
        ):
            raise ValueError("approved tool request is no longer available")
        approval = self.store.resolve_approval(approval_id, decision)
        self._emit(task_id, "approval.resolved", {"approval": approval})
        private_payload = self._pending_approval_calls.pop(approval_id, None)
        if decision == "denied":
            self.store.update_task(task_id, status="cancelled", error="Approval denied")
            self._emit(task_id, "task.cancelled", {"message": "Approval denied"})
            return approval
        if approval["kind"] == "plan":
            self._launch(task_id, self._drive(task_id))
        elif approval["kind"] == "tool":
            assert private_payload is not None
            self._launch(task_id, self._resume_approved(task_id, private_payload))
        return approval

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] not in ACTIVE_STATUSES:
            return task
        cancel = self._cancel.get(task_id)
        if cancel is not None:
            cancel.set()
            self.store.update_task(task_id, status="cancelling")
            self._emit(task_id, "task.status", {"status": "cancelling"})
        else:
            self.store.update_task(task_id, status="cancelled", error="Cancelled by user")
            self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})
            self._drop_pending_approvals(task_id)
        return self._task(task_id)

    async def takeover(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] not in ACTIVE_STATUSES:
            return task
        self.cancel(task_id)
        await self._computer.release_all()
        if not any(event["type"] == "control.takeover" for event in self.store.events(task_id)):
            self._emit(task_id, "control.takeover", {"message": "You have control"})
        self._mark_cancelled(task_id)
        return self._task(task_id)

    def steer(self, task_id: str, message: str) -> dict[str, Any]:
        task = self._task(task_id)
        message = message.strip()
        if not message:
            raise ValueError("steering message is required")
        if task["status"] not in {"running", "awaiting_approval"}:
            raise ValueError("only an active task can be steered")
        self._steering[task_id].append(message)
        return self._emit(task_id, "task.steered", {"message": message})

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]]:
        self._task(task_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(task_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    async def close(self) -> None:
        for event in self._cancel.values():
            event.set()
        workers = list(self._workers.values())
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await self._computer.release_all()
        await asyncio.to_thread(self.store.prune)

    # Execution -------------------------------------------------------

    async def _drive(
        self,
        task_id: str,
        *,
        history: list[dict[str, Any]] | None = None,
        step: int | None = None,
        started_at: float | None = None,
    ) -> None:
        task = self._task(task_id)
        runtime = task.get("runtime") or {}
        history = list(history if history is not None else runtime.get("history") or [])
        step = int(step if step is not None else runtime.get("step") or 0)
        started_at = started_at or float(runtime.get("started_at") or monotonic())
        cancel = self._cancel.setdefault(task_id, asyncio.Event())
        self.store.update_task(task_id, status="running")
        self._emit(task_id, "task.status", {"status": "running"})
        try:
            while True:
                task = self._task(task_id)
                limits = task["limits"]
                if cancel.is_set():
                    await self._cancelled(task_id)
                    return
                if step >= limits["max_steps"]:
                    raise RuntimeError("Agent stopped at the configured step limit")
                if monotonic() - started_at >= limits["max_seconds"]:
                    raise RuntimeError("Agent stopped at the configured time limit")
                steering = self._steering.pop(task_id, [])
                for message in steering:
                    history.append({"role": "user", "content": message})
                provider = dict(self._provider(task["provider_id"]))
                provider["model"] = str(task.get("model") or _resolve_model(provider, None))
                adapter = self._adapter(provider)
                manifest = runtime.get("manifest") or await asyncio.to_thread(workspace_manifest, task["cwd"])
                read_only = task.get("mode") == "ask"
                turn = await self._provider_turn(
                    adapter,
                    cancel,
                    prompt=task["prompt"],
                    cwd=task["cwd"],
                    manifest=manifest,
                    input_items=history if history else None,
                    allow_computer=(not read_only) and "computer" in provider["capabilities"],
                    read_only=read_only,
                )
                if cancel.is_set():
                    await self._cancelled(task_id)
                    return
                self.store.update_task(task_id, previous_response_id=turn.response_id)
                if turn.text:
                    self._emit(task_id, "assistant.message", {"text": turn.text})
                if turn.usage:
                    self._emit(task_id, "provider.usage", {"usage": turn.usage})
                history.extend(turn.output_items)
                if not turn.calls:
                    result = turn.text or "Task completed."
                    self.store.update_task(task_id, status="completed", result=result, runtime={})
                    self._emit(task_id, "task.completed", {"result": result})
                    return
                paused, history, step = await self._run_calls(
                    task_id,
                    turn.calls,
                    history,
                    step,
                    started_at,
                )
                if paused:
                    return
                runtime = {"manifest": manifest, "history": history, "step": step, "started_at": started_at}
                self.store.update_task(task_id, runtime=runtime)
        except asyncio.CancelledError:
            await self._cancelled(task_id)
        except Exception as exc:
            if cancel.is_set():
                await self._cancelled(task_id)
            else:
                self._fail(task_id, exc)
        finally:
            self._cancel.pop(task_id, None)

    async def _run_calls(
        self,
        task_id: str,
        calls: list[ProviderCall],
        history: list[dict[str, Any]],
        step: int,
        started_at: float,
        *,
        approved_first: bool = False,
    ) -> tuple[bool, list[dict[str, Any]], int]:
        task = self._task(task_id)
        read_only = task.get("mode") == "ask"
        for index, call in enumerate(calls):
            if read_only and call.type == "function" and call.name == "run_shell":
                # Read-only Ask mode never enters the approval flow: mutating
                # commands are refused inline so the model can adjust.
                output = await self._execute_call(task_id, call)
                history.extend(output if isinstance(output, list) else [output])
                step += 1
                continue
            if read_only and call.type == "computer":
                raise RuntimeError("Ask mode cannot use the computer. Switch to Agent mode.")
            decision = self._decision(call, task["cwd"])
            if decision.approval_required and not (approved_first and index == 0):
                public_payload = {
                    "title": decision.reason,
                    "consequence": decision.consequence,
                    "call": call.public(),
                    "remaining_calls": [item.public() for item in calls[index + 1 :]],
                    "history": self._public_value(history),
                    "step": step,
                    "started_at": started_at,
                }
                private_payload = {
                    **public_payload,
                    "task_id": task_id,
                    "call": self._call_payload(call),
                    "remaining_calls": [self._call_payload(item) for item in calls[index + 1 :]],
                    "history": history,
                }
                approval = self.store.create_approval(task_id, "tool", public_payload)
                self._pending_approval_calls[approval["id"]] = private_payload
                self.store.update_task(task_id, status="awaiting_approval")
                self._emit(task_id, "approval.requested", {"approval": approval})
                self._emit(task_id, "task.status", {"status": "awaiting_approval"})
                return True, history, step
            output = await self._execute_call(task_id, call)
            history.extend(output if isinstance(output, list) else [output])
            step += 1
        return False, history, step

    async def _resume_approved(self, task_id: str, payload: dict[str, Any]) -> None:
        try:
            call = self._call(payload["call"])
            remaining = [self._call(item) for item in payload.get("remaining_calls") or []]
            history = list(payload.get("history") or [])
            step = int(payload.get("step") or 0)
            started_at = float(payload.get("started_at") or monotonic())
            paused, history, step = await self._run_calls(
                task_id,
                [call, *remaining],
                history,
                step,
                started_at,
                approved_first=True,
            )
            if not paused:
                await self._drive(task_id, history=history, step=step, started_at=started_at)
        except asyncio.CancelledError:
            await self._cancelled(task_id)
        except Exception as exc:
            self._fail(task_id, exc)

    async def _execute_call(
        self,
        task_id: str,
        call: ProviderCall,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        task = self._task(task_id)
        cancel = self._cancel.setdefault(task_id, asyncio.Event())
        if cancel.is_set():
            raise asyncio.CancelledError
        self._emit(task_id, "tool.started", {"call": call.public()})
        if call.type == "function" and call.name == "run_shell":
            command = str(call.arguments.get("command") or "")
            if not command:
                raise ValueError("provider requested an empty shell command")
            if task.get("mode") == "ask" and is_mutating_shell(command):
                # Ask mode is read-only: surface the refusal as a tool result so the
                # model can adjust rather than failing the whole conversation turn.
                refusal = {
                    "ok": False,
                    "exit_code": None,
                    "output": "Refused: Ask mode is read-only. Switch to Agent mode to change files or reach the network.",
                    "refused": True,
                }
                self._emit(task_id, "tool.finished", {"call_id": call.call_id, "result": refusal})
                return {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(refusal, ensure_ascii=False),
                }
            timeout = min(
                float(task["limits"]["shell_timeout_s"]),
                max(1.0, float(call.arguments.get("timeout_s") or task["limits"]["shell_timeout_s"])),
            )
            result = await run_shell(command, task["cwd"], timeout_s=timeout, cancel=cancel)
            public = result.public()
            self._emit(task_id, "tool.finished", {"call_id": call.call_id, "result": public})
            if result.cancelled:
                raise asyncio.CancelledError
            return {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(public, ensure_ascii=False),
            }
        if call.type == "function" and call.name == "share_file":
            result = await self._share_file(task_id, task, call)
            self._emit(task_id, "tool.finished", {"call_id": call.call_id, "name": call.name, "result": result})
            return {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            }
        if call.type == "function" and call.name == "spawn_subagent":
            if task.get("mode") == "ask":
                refusal = {
                    "ok": False,
                    "refused": True,
                    "output": "Refused: Ask mode cannot spawn sub-agents. Switch to Agent mode.",
                }
                self._emit(task_id, "tool.finished", {"call_id": call.call_id, "name": call.name, "result": refusal})
                return {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(refusal, ensure_ascii=False),
                }
            result = await self._spawn_subagent(task_id, task, call, cancel)
            self._emit(task_id, "tool.finished", {"call_id": call.call_id, "name": call.name, "result": result})
            return {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result, ensure_ascii=False),
            }
        if call.type == "computer":
            screenshot = await self._computer.execute(call.actions, cancel=cancel)
            artifact = self.store.save_artifact(task_id, "screenshot", "image/jpeg", screenshot)
            self._emit(task_id, "computer.screenshot", {"artifact": artifact})
            self._emit(task_id, "tool.finished", {"call_id": call.call_id, "artifact": artifact})
            image = base64.b64encode(screenshot).decode("ascii")
            if call.name == "use_computer":
                return [
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": "Computer action completed.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Inspect the screenshot and continue the approved task.",
                            },
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{image}",
                            },
                        ],
                    },
                ]
            return {
                "type": "computer_call_output",
                "call_id": call.call_id,
                "output": {"type": "computer_screenshot", "image_url": f"data:image/jpeg;base64,{image}"},
                "acknowledged_safety_checks": call.safety_checks,
            }
        raise ValueError(f"unsupported provider tool: {call.name or call.type}")

    # Helpers ---------------------------------------------------------

    async def _share_file(
        self,
        task_id: str,
        task: dict[str, Any],
        call: ProviderCall,
    ) -> dict[str, Any]:
        raw_path = str(call.arguments.get("path") or "").strip()
        caption = str(call.arguments.get("caption") or "").strip()
        if not raw_path:
            return {"ok": False, "error": "share_file requires a path"}
        root = Path(task["cwd"]).resolve()
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return {"ok": False, "error": f"File not found: {raw_path}"}
        if resolved != root and root not in resolved.parents:
            return {"ok": False, "error": "share_file stays inside the project folder"}
        if not resolved.is_file():
            return {"ok": False, "error": f"Not a file: {raw_path}"}
        size = resolved.stat().st_size
        if size > _SHARE_MAX_BYTES:
            return {"ok": False, "error": f"File too large to share ({size} bytes, max {_SHARE_MAX_BYTES})"}
        data = await asyncio.to_thread(resolved.read_bytes)
        mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        artifact = await asyncio.to_thread(self.store.save_artifact, task_id, "media", mime, data)
        self._emit(
            task_id,
            "agent.media",
            {"artifact": artifact, "name": resolved.name, "caption": caption, "call_id": call.call_id},
        )
        return {
            "ok": True,
            "shared": resolved.name,
            "artifact": {"id": artifact["id"], "mime": artifact["mime"], "size": artifact["size"]},
        }

    async def _spawn_subagent(
        self,
        task_id: str,
        task: dict[str, Any],
        call: ProviderCall,
        cancel: asyncio.Event,
    ) -> dict[str, Any]:
        prompt = str(call.arguments.get("task") or "").strip()
        if not prompt:
            return {"ok": False, "error": "spawn_subagent requires a task"}
        agent = str(call.arguments.get("agent") or "").strip() or "Sub-agent"
        instructions = str(call.arguments.get("instructions") or "").strip()
        child_prompt = prompt
        if instructions:
            child_prompt = (
                f'You are the "{agent}" sub-agent. Follow these instructions:\n'
                f"{instructions}\n\nTask: {prompt}"
            )
        parent_limits = task["limits"]
        child = await self.create_task(
            prompt=child_prompt,
            cwd=task["cwd"],
            provider_id=task["provider_id"],
            limits={
                "max_steps": min(24, parent_limits["max_steps"]),
                "max_seconds": min(900, parent_limits["max_seconds"]),
                "shell_timeout_s": parent_limits["shell_timeout_s"],
            },
            mode="agent",
            model=str(task.get("model") or "") or None,
        )
        child_id = child["id"]
        if child["status"] in {"completed", "failed", "cancelled"}:
            self._emit(
                task_id,
                "subagent.started",
                {"call_id": call.call_id, "agent": agent, "task": prompt, "child_id": child_id},
            )
            self._emit(
                task_id,
                "subagent.finished",
                {
                    "call_id": call.call_id,
                    "child_id": child_id,
                    "agent": agent,
                    "status": child["status"],
                    "result": str(child.get("error") or child.get("result") or ""),
                },
            )
            return {
                "ok": child["status"] == "completed",
                "status": child["status"],
                "result": str(child.get("error") or child.get("result") or "")[:4000],
                "task_id": child_id,
            }
        # The parent task was already approved, so the delegated child runs
        # autonomously under that approval: resolve its plan and tool checks.
        await self._auto_approve(child_id)
        self._emit(
            task_id,
            "subagent.started",
            {"call_id": call.call_id, "agent": agent, "task": prompt, "child_id": child_id},
        )
        queue = self.subscribe(child_id)
        deadline = monotonic() + min(parent_limits["max_seconds"], _SUBAGENT_MAX_SECONDS)
        status = "failed"
        result = ""
        try:
            while True:
                if cancel.is_set():
                    self.cancel(child_id)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.4)
                except asyncio.TimeoutError:
                    current = self._task(child_id)
                    if current["status"] not in ACTIVE_STATUSES:
                        status = current["status"]
                        result = str(current.get("result") or current.get("error") or "")
                        break
                    if monotonic() >= deadline:
                        self.cancel(child_id)
                    continue
                event_type = event["type"]
                payload = event.get("payload") or {}
                if event_type == "approval.requested":
                    approval = payload.get("approval") or {}
                    if approval.get("id"):
                        asyncio.create_task(self._auto_approve(child_id, approval["id"]))
                self._emit(
                    task_id,
                    "subagent.event",
                    {
                        "call_id": call.call_id,
                        "child_id": child_id,
                        "agent": agent,
                        "type": event_type,
                        "payload": _slim_payload(payload),
                    },
                )
                if event_type in {"task.completed", "task.failed", "task.cancelled"}:
                    status = {
                        "task.completed": "completed",
                        "task.failed": "failed",
                        "task.cancelled": "cancelled",
                    }[event_type]
                    result = str(payload.get("result") or payload.get("message") or "")
                    break
        finally:
            self.unsubscribe(child_id, queue)
        self._emit(
            task_id,
            "subagent.finished",
            {
                "call_id": call.call_id,
                "child_id": child_id,
                "agent": agent,
                "status": status,
                "result": result,
            },
        )
        return {
            "ok": status == "completed",
            "status": status,
            "result": result[:4000],
            "task_id": child_id,
        }

    async def _auto_approve(self, task_id: str, approval_id: str | None = None) -> None:
        approvals = self.store.approvals(task_id)
        for approval in approvals:
            if approval["status"] != "pending":
                continue
            if approval_id is not None and approval["id"] != approval_id:
                continue
            try:
                await self.resolve_approval(task_id, approval["id"], "approved")
            except (KeyError, ValueError):
                continue

    def _upload_message(self, task_id: str, artifact_ids: list[str]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": "The user attached these images to the message."}]
        for artifact_id in artifact_ids:
            artifact = self.store.get_artifact(task_id, artifact_id)
            if artifact is None or not str(artifact.get("mime") or "").startswith("image/"):
                continue
            encoded = base64.b64encode(Path(artifact["path"]).read_bytes()).decode("ascii")
            parts.append({"type": "input_image", "image_url": f"data:{artifact['mime']};base64,{encoded}"})
        return {"role": "user", "content": parts}

    def _emit(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.store.append_event(task_id, event_type, payload)
        for queue in tuple(self._subscribers.get(task_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        return event

    def _launch(self, task_id: str, coroutine: Any) -> None:
        current = self._workers.get(task_id)
        if current is not None and not current.done():
            raise ValueError("task is already running")
        worker = asyncio.create_task(coroutine, name=f"termx-agent-{task_id[:8]}")
        self._workers[task_id] = worker
        worker.add_done_callback(lambda _done: self._workers.pop(task_id, None))

    def _provider(self, provider_id: str) -> dict[str, Any]:
        provider = self.store.get_provider(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return provider

    def _adapter(self, provider: dict[str, Any]) -> ProviderAdapter:
        key = self.credentials.get(provider["id"]) or ""
        return self._adapter_factory(provider, key)

    @staticmethod
    def _default_adapter(provider: dict[str, Any], key: str) -> ProviderAdapter:
        return OpenAIResponsesAdapter(
            base_url=provider["base_url"],
            model=provider["model"],
            api_key=key,
            capabilities=provider["capabilities"],
            native_computer=provider["kind"] == "openai",
        )

    def _task(self, task_id: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    @staticmethod
    def _limits(value: dict[str, Any] | None) -> dict[str, Any]:
        source = value or {}
        return {
            "max_steps": min(100, max(1, int(source.get("max_steps") or DEFAULT_LIMITS["max_steps"]))),
            "max_seconds": min(3600, max(30, int(source.get("max_seconds") or DEFAULT_LIMITS["max_seconds"]))),
            "shell_timeout_s": min(600, max(5, int(source.get("shell_timeout_s") or DEFAULT_LIMITS["shell_timeout_s"]))),
        }

    @staticmethod
    def _call(value: dict[str, Any]) -> ProviderCall:
        return ProviderCall(
            type=str(value.get("type") or ""),
            call_id=str(value.get("call_id") or ""),
            name=str(value.get("name") or ""),
            arguments=value.get("arguments") if isinstance(value.get("arguments"), dict) else {},
            actions=value.get("actions") if isinstance(value.get("actions"), list) else [],
            safety_checks=value.get("safety_checks") if isinstance(value.get("safety_checks"), list) else [],
        )

    @staticmethod
    def _decision(call: ProviderCall, cwd: str) -> PolicyDecision:
        if call.type == "function" and call.name == "run_shell":
            return evaluate_shell(str(call.arguments.get("command") or ""), cwd)
        if call.type == "computer":
            if call.safety_checks:
                detail = "; ".join(
                    str(item.get("message") or item.get("code") or "Provider flagged this action")
                    for item in call.safety_checks
                )
                return PolicyDecision(
                    True,
                    True,
                    "Provider safety check",
                    detail[:500],
                )
            return evaluate_computer(call.actions)
        return PolicyDecision(False, True, "Unknown tool", "Runs an unsupported tool request")

    @staticmethod
    def _call_payload(call: ProviderCall) -> dict[str, Any]:
        return {
            "type": call.type,
            "call_id": call.call_id,
            "name": call.name,
            "arguments": call.arguments,
            "actions": call.actions,
            "safety_checks": call.safety_checks,
        }

    @staticmethod
    def _public_value(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [AgentManager._public_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): AgentManager._public_value(item)
                for key, item in value.items()
            }
        return value

    def _drop_pending_approvals(self, task_id: str) -> None:
        for approval_id, payload in list(self._pending_approval_calls.items()):
            if payload["task_id"] == task_id:
                self._pending_approval_calls.pop(approval_id, None)

    async def _cancelled(self, task_id: str) -> None:
        if self._task(task_id)["status"] == "cancelled":
            return
        await self._computer.release_all()
        self._mark_cancelled(task_id)

    def _mark_cancelled(self, task_id: str) -> None:
        if self._task(task_id)["status"] == "cancelled":
            return
        self.store.update_task(task_id, status="cancelled", error="Cancelled by user")
        self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})

    @staticmethod
    async def _provider_turn(
        adapter: ProviderAdapter,
        cancel: asyncio.Event,
        *,
        prompt: str,
        cwd: str,
        manifest: dict[str, Any],
        input_items: list[dict[str, Any]] | None,
        allow_computer: bool,
        read_only: bool,
    ) -> ProviderTurn:
        turn = asyncio.create_task(
            adapter.turn(
                prompt=prompt,
                cwd=cwd,
                manifest=manifest,
                input_items=input_items,
                allow_computer=allow_computer,
                read_only=read_only,
            )
        )
        cancelled = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({turn, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done:
                turn.cancel()
                await asyncio.gather(turn, return_exceptions=True)
                raise asyncio.CancelledError
            return await turn
        finally:
            cancelled.cancel()
            if not turn.done():
                turn.cancel()
            await asyncio.gather(cancelled, turn, return_exceptions=True)

    def _fail(self, task_id: str, exc: Exception) -> None:
        if isinstance(exc, ProviderError):
            message = str(exc)
        elif isinstance(exc, (ValueError, RuntimeError, OSError)):
            message = str(exc)
        else:
            message = "Agent run failed"
        self.store.update_task(task_id, status="failed", error=message)
        self._emit(task_id, "task.failed", {"message": message})


_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_SHARE_MAX_BYTES = 16 * 1024 * 1024
_SUBAGENT_MAX_SECONDS = 1800


def _slim_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop transcript-sized fields before forwarding a child event to the parent stream."""
    slim = {
        str(key): value
        for key, value in payload.items()
        if key not in {"history", "remaining_calls"}
    }
    approval = slim.get("approval")
    if isinstance(approval, dict):
        inner = dict(approval.get("payload") or {})
        inner.pop("history", None)
        inner.pop("remaining_calls", None)
        slim["approval"] = {**approval, "payload": inner}
    return slim


def _resolve_model(provider: dict[str, Any], requested: str | None) -> str:
    models = configured_models(str(provider.get("model") or ""))
    if not models:
        raise ValueError("provider has no model configured")
    chosen = (requested or "").strip()
    if not chosen:
        return models[0]
    if chosen not in models:
        raise ValueError("model is not configured on this provider")
    return chosen


def _decode_images(attachments: list[dict[str, Any]] | None) -> list[tuple[str, str, bytes]]:
    images: list[tuple[str, str, bytes]] = []
    for item in attachments or []:
        mime = str(item.get("mime") or "")
        if mime not in _IMAGE_TYPES:
            raise ValueError("only JPEG, PNG, GIF, and WebP images can be attached")
        try:
            raw = base64.b64decode(str(item.get("data") or ""), validate=True)
        except Exception as exc:
            raise ValueError("image data is not valid base64") from exc
        if not raw or len(raw) > 2_000_000:
            raise ValueError("each image must be under 2 MB")
        name = str(item.get("name") or "image")[:180]
        images.append((name, mime, raw))
    if len(images) > 4:
        raise ValueError("attach at most 4 images")
    return images
