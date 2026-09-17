from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from termx.agent.computer import ComputerController
from termx.agent.context import workspace_manifest
from termx.agent.execution import run_shell
from termx.agent.policy import PolicyDecision, evaluate_computer, evaluate_shell, is_mutating_shell
from termx.agent.providers import (
    OpenAIResponsesAdapter,
    ProviderAdapter,
    ProviderCall,
    ProviderError,
)
from termx.agent.secrets import CredentialStore
from termx.agent.store import ACTIVE_STATUSES, AgentStore

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
    ) -> dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("task prompt is required")
        mode = "ask" if mode == "ask" else "agent"
        root = Path(cwd).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("project folder is not a directory")
        provider = self._provider(provider_id)
        adapter = self._adapter(provider)
        bounded = self._limits(limits)
        task = self.store.create_task(
            prompt=prompt,
            cwd=str(root),
            provider_id=provider_id,
            model=provider["model"],
            limits=bounded,
            mode=mode,
        )
        task_id = task["id"]
        self._emit(task_id, "task.created", {"task": task})
        try:
            manifest = await asyncio.to_thread(workspace_manifest, str(root))
            if mode == "ask":
                # Ask mode is read-only and low-risk, so it starts immediately
                # instead of waiting for plan approval.
                runtime = {"manifest": manifest, "history": [], "step": 0, "started_at": None}
                self.store.update_task(task_id, runtime=runtime)
                self._launch(task_id, self._drive(task_id))
                return self.store.get_task(task_id, include_events=True) or task
            plan, response_id = await adapter.plan(prompt, str(root), manifest)
            runtime = {"manifest": manifest, "history": [], "step": 0, "started_at": None}
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
        approval = self.store.resolve_approval(approval_id, decision)
        self._emit(task_id, "approval.resolved", {"approval": approval})
        if decision == "denied":
            self.store.update_task(task_id, status="cancelled", error="Approval denied")
            self._emit(task_id, "task.cancelled", {"message": "Approval denied"})
            return approval
        if approval["kind"] == "plan":
            self._launch(task_id, self._drive(task_id))
        elif approval["kind"] == "tool":
            self._launch(task_id, self._resume_approved(task_id, approval["payload"]))
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
        return self._task(task_id)

    async def takeover(self, task_id: str) -> dict[str, Any]:
        task = self.cancel(task_id)
        await self._computer.release_all()
        self._emit(task_id, "control.takeover", {"message": "You have control"})
        return task

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
                provider = self._provider(task["provider_id"])
                adapter = self._adapter(provider)
                manifest = runtime.get("manifest") or await asyncio.to_thread(workspace_manifest, task["cwd"])
                read_only = task.get("mode") == "ask"
                turn = await adapter.turn(
                    prompt=task["prompt"],
                    cwd=task["cwd"],
                    manifest=manifest,
                    input_items=history if history else None,
                    allow_computer=(not read_only) and "computer" in provider["capabilities"],
                    read_only=read_only,
                )
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
                history.append(output)
                step += 1
                continue
            if read_only and call.type == "computer":
                raise RuntimeError("Ask mode cannot use the computer. Switch to Agent mode.")
            decision = self._decision(call, task["cwd"])
            if decision.approval_required and not (approved_first and index == 0):
                payload = {
                    "title": decision.reason,
                    "consequence": decision.consequence,
                    "call": call.public(),
                    "remaining_calls": [item.public() for item in calls[index + 1 :]],
                    "history": history,
                    "step": step,
                    "started_at": started_at,
                }
                approval = self.store.create_approval(task_id, "tool", payload)
                self.store.update_task(task_id, status="awaiting_approval")
                self._emit(task_id, "approval.requested", {"approval": approval})
                self._emit(task_id, "task.status", {"status": "awaiting_approval"})
                return True, history, step
            output = await self._execute_call(task_id, call)
            history.append(output)
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

    async def _execute_call(self, task_id: str, call: ProviderCall) -> dict[str, Any]:
        task = self._task(task_id)
        cancel = self._cancel.setdefault(task_id, asyncio.Event())
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
        if call.type == "computer":
            screenshot = await self._computer.execute(call.actions)
            artifact = self.store.save_artifact(task_id, "screenshot", "image/jpeg", screenshot)
            self._emit(task_id, "computer.screenshot", {"artifact": artifact})
            self._emit(task_id, "tool.finished", {"call_id": call.call_id, "artifact": artifact})
            image = base64.b64encode(screenshot).decode("ascii")
            return {
                "type": "computer_call_output",
                "call_id": call.call_id,
                "output": {"type": "computer_screenshot", "image_url": f"data:image/jpeg;base64,{image}"},
                "acknowledged_safety_checks": call.safety_checks,
            }
        raise ValueError(f"unsupported provider tool: {call.name or call.type}")

    # Helpers ---------------------------------------------------------

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

    async def _cancelled(self, task_id: str) -> None:
        await self._computer.release_all()
        self.store.update_task(task_id, status="cancelled", error="Cancelled by user")
        self._emit(task_id, "task.cancelled", {"message": "Cancelled by user"})

    def _fail(self, task_id: str, exc: Exception) -> None:
        if isinstance(exc, ProviderError):
            message = str(exc)
        elif isinstance(exc, (ValueError, RuntimeError, OSError)):
            message = str(exc)
        else:
            message = "Agent run failed"
        self.store.update_task(task_id, status="failed", error=message)
        self._emit(task_id, "task.failed", {"message": message})
