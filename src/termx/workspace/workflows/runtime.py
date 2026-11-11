"""Bounded JSON IPC to a constrained interpreter inside a restricted process.

QuickJS constrains the guest's language capabilities. SandboxRunner supplies
the separate OS filesystem/network boundary; neither is advertised as the
other. A missing dependency or restricted backend never falls back to Node or
unrestricted host execution.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from termx.sandbox import ResourceLimits, SandboxFailure, SpawnSpec, runner_for

HostCall = Callable[[str, str, dict[str, Any]], Awaitable[Any]]
DEFAULT_LIMITS: dict[str, None] = {
    "max_agents": None,
    "concurrency": None,
    "timeout_s": None,
}
# These constrain individual values and the interpreter's memory, not the
# amount of work a workflow may perform over its lifetime.
SAFETY_CONSTRAINTS: dict[str, int] = {
    "heap_bytes": 32 << 20,
    "stack_bytes": 256 << 10,
    "output_bytes": 262_144,
    "frame_bytes": 524_288,
}
_SAFETY_BOUNDS = {
    "heap_bytes": (1 << 20, 64 << 20), "stack_bytes": (64 << 10, 512 << 10),
    "output_bytes": (1024, 524_288),
    "frame_bytes": (1024, 1_048_576),
}
_MAX_SOURCE_BYTES = 65_536
_MAX_ARGS_BYTES = 65_536
_MAX_START_BYTES = 1_048_576
_HELPERS = ["agent", "parallel", "pipeline", "phase", "log"]


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_limits(limits: dict | None = None) -> dict[str, int | float | None]:
    """Null run quotas are unlimited; safety overrides are host-only inputs."""
    result = {**DEFAULT_LIMITS, **SAFETY_CONSTRAINTS}
    if limits is None:
        return result
    if not isinstance(limits, dict):
        raise RuntimeFailure("invalid_limits", "Workflow limits must be an object")
    for key, value in limits.items():
        if key not in DEFAULT_LIMITS and key not in _SAFETY_BOUNDS:
            raise RuntimeFailure("invalid_limits", f"Unsupported workflow limit: {key}")
        if key in DEFAULT_LIMITS and value is None:
            result[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            raise RuntimeFailure("invalid_limits", f"Invalid workflow limit: {key}")
        if key in _SAFETY_BOUNDS:
            low, high = _SAFETY_BOUNDS[key]
            if value < low or value > high:
                raise RuntimeFailure("invalid_limits", f"Workflow safety constraint {key} must be between {low} and {high}")
        elif value <= 0:
            raise RuntimeFailure("invalid_limits", f"Workflow {key} must be positive or null")
        if key == "timeout_s":
            try:
                value = float(value)
            except OverflowError as exc:
                raise RuntimeFailure("invalid_limits", "Workflow timeout_s must be a finite duration or null") from exc
            if not math.isfinite(value):
                raise RuntimeFailure("invalid_limits", "Workflow timeout_s must be a finite duration or null")
        if key != "timeout_s":
            if value != int(value):
                raise RuntimeFailure("invalid_limits", f"Workflow {key} must be an integer")
            value = int(value)
        result[key] = value
    if result["concurrency"] is not None and result["max_agents"] is not None:
        result["concurrency"] = min(result["concurrency"], result["max_agents"])
    return result


def _encoded(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise RuntimeFailure("invalid_json", "Workflow values must be finite JSON data") from exc


def _error(exc: BaseException) -> dict[str, str]:
    code = getattr(exc, "code", None) or getattr(exc, "reason", None) or "runtime_error"
    return {"code": str(code)[:80], "message": (str(exc) or type(exc).__name__)[:2000]}


def _check_source(source: Any) -> None:
    if not isinstance(source, str) or not source.strip():
        raise RuntimeFailure("invalid_source", "Workflow source must be a nonempty JavaScript function body")
    if len(source.encode()) > _MAX_SOURCE_BYTES:
        raise RuntimeFailure("source_limit", "Workflow source exceeds 65536 UTF-8 bytes")


class _Frames:
    """Read bounded lines without depending on StreamReader's 64-KiB readline limit."""

    def __init__(self, reader: Any, maximum: int) -> None:
        self.reader = reader
        self.maximum = maximum
        self.buffer = bytearray()
        self.consumed = 0

    async def read(self) -> dict[str, Any]:
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                if newline > self.maximum:
                    raise RuntimeFailure("frame_limit", "Workflow worker frame exceeds the limit")
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                self.consumed += newline + 1
                try:
                    item = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise RuntimeFailure("invalid_protocol", "Workflow worker emitted invalid JSON") from exc
                if not isinstance(item, dict):
                    raise RuntimeFailure("invalid_protocol", "Workflow worker frame must be an object")
                return item
            if len(self.buffer) > self.maximum:
                raise RuntimeFailure("frame_limit", "Workflow worker frame exceeds the limit")
            chunk = await self.reader.read(65_536)
            if not chunk:
                raise RuntimeFailure("worker_exited", "Workflow worker exited without a terminal result")
            self.buffer.extend(chunk)


class WorkflowRuntime:
    def __init__(self, *, runner: Any = None, python_executable: str | None = None, worker_path: str | Path | None = None) -> None:
        self._runner = runner
        self._python = python_executable or sys.executable
        self._frozen_worker = bool(getattr(sys, "frozen", False) and python_executable is None)
        self._worker = Path(worker_path) if worker_path else Path(__file__).with_name("worker.py")

    def _worker_command(self) -> tuple[str, ...]:
        if self._frozen_worker:
            # The sidecar has one exact allowlisted worker entry; it does not
            # act as Python or accept arbitrary module/script targets.
            return (self._python, "--runtime-module", "termx.workspace.workflows.worker")
        return (self._python, "-I", "-B", "-u", str(self._worker.resolve()))

    def _worker_read_roots(self) -> tuple[str, ...]:
        paths = [self._worker.parent, Path(sys.prefix), Path(sys.base_prefix)]
        if self._frozen_worker:
            paths.append(Path(self._python).resolve().parent)
            if getattr(sys, "_MEIPASS", None):
                paths.append(Path(sys._MEIPASS))
        return tuple(dict.fromkeys(str(path.resolve()) for path in paths))

    @staticmethod
    def _dependency() -> tuple[bool, str | None]:
        try:
            version = importlib.metadata.version("quickjs-ng")
            return importlib.util.find_spec("quickjs") is not None, version
        except (importlib.metadata.PackageNotFoundError, ImportError, ValueError):
            return False, None

    def _select_runner(self, state_dir: str | Path) -> Any:
        runner = self._runner or runner_for("agent", state_dir=state_dir)
        capabilities = runner.capabilities()
        if not (capabilities.filesystem_isolation and capabilities.network_control and capabilities.process_tree_kill):
            raise RuntimeFailure("sandbox_unavailable", "Workflows require an enforced filesystem/network sandbox; host fallback is disabled")
        if capabilities.backend == "macos-helper":
            raise RuntimeFailure("sandbox_ipc_unsupported", "The macOS helper does not yet support workflow input pipes")
        return runner

    def capabilities(self) -> dict[str, Any]:
        installed, version = self._dependency()
        result: dict[str, Any] = {
            "available": False, "reason": None, "engine": "quickjs-ng", "version": version,
            "helpers": list(_HELPERS), "noHostIO": True, "no_host_io": True,
            "python_callbacks": False, "module_loading": False,
            "limits": dict(DEFAULT_LIMITS), "safety_constraints": dict(SAFETY_CONSTRAINTS),
            "replay": False,
            "process_boundary": {"available": False},
        }
        if not installed:
            result["reason"] = "JavaScript workflows are not installed on this host"
            result["code"] = "runtime_unavailable"
            return result
        if not self._worker.is_file() or not self._worker.with_name("bridge.js").is_file():
            result["reason"] = "The workflow worker or JavaScript bridge is missing from this installation"
            result["code"] = "worker_assets_missing"
            return result
        try:
            runner = self._select_runner(Path(tempfile.gettempdir()) / "termx-workflow-capabilities")
            result["process_boundary"] = {"available": True, **runner.capabilities().public()}
        except (RuntimeFailure, SandboxFailure, OSError) as exc:
            result["reason"] = str(exc)
            result["code"] = _error(exc)["code"]
            return result
        result["available"] = True
        return result

    async def validate(self, source: str, limits: dict | None = None) -> dict[str, Any]:
        try:
            _check_source(source)
            normalized = normalize_limits(limits)
        except (RuntimeFailure, UnicodeError) as exc:
            return {"valid": False, "error": _error(exc)}
        capability = self.capabilities()
        if not capability["available"]:
            return {"valid": False, "unsupported": True, "error": {"code": capability.get("code", "runtime_unavailable"), "message": capability["reason"]}}
        outcome = await self._execute("validate", source, {}, normalized, None, asyncio.Event())
        return {"valid": True} if outcome["status"] == "completed" else {"valid": False, "error": outcome["error"]}

    async def run(self, source: str, args: Any, limits: dict | None, on_call: HostCall, cancel_event: asyncio.Event) -> dict[str, Any]:
        try:
            _check_source(source)
            normalized = normalize_limits(limits)
            if len(_encoded(args)) > _MAX_ARGS_BYTES:
                raise RuntimeFailure("argument_limit", "Workflow arguments exceed 65536 JSON bytes")
        except (RuntimeFailure, UnicodeError) as exc:
            return {"status": "failed", "result": None, "error": _error(exc)}
        if cancel_event.is_set():
            return {"status": "cancelled", "result": None, "error": None}
        capability = self.capabilities()
        if not capability["available"]:
            return {"status": "failed", "result": None, "error": {"code": capability.get("code", "runtime_unavailable"), "message": capability["reason"]}}
        return await self._execute("run", source, args, normalized, on_call, cancel_event)

    async def _execute(self, mode: str, source: str, arguments: Any, limits: dict, on_call: HostCall | None, cancel_event: asyncio.Event) -> dict[str, Any]:
        process = None
        tasks: set[asyncio.Task] = set()
        reader_task: asyncio.Task | None = None
        cancel_task: asyncio.Task | None = None
        outcome: dict[str, Any] = {"status": "failed", "result": None, "error": {"code": "worker_exited", "message": "Workflow worker did not return a result"}}
        with tempfile.TemporaryDirectory(prefix="termx-workflow-") as temporary:
            try:
                runner = self._select_runner(temporary)
                spec = SpawnSpec(
                    profile="agent", argv=self._worker_command(),
                    cwd=temporary, workspace_root=temporary, read_only_roots=self._worker_read_roots(),
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, network="none",
                    stdin=asyncio.subprocess.PIPE, purpose="workflow-interpreter",
                    limits=ResourceLimits(output_bytes=limits["frame_bytes"]),
                )
                process = await runner.spawn(spec)
                pipes = getattr(process, "process", None)
                if pipes is None or pipes.stdin is None or pipes.stdout is None:
                    raise RuntimeFailure("sandbox_ipc_unsupported", "Restricted runner did not provide workflow input/output pipes")
                writer = pipes.stdin
                write_lock = asyncio.Lock()
                closing = False

                async def send(frame: dict, maximum: int = limits["frame_bytes"]) -> None:
                    data = _encoded(frame) + b"\n"
                    if len(data) > maximum:
                        raise RuntimeFailure("frame_limit", "Workflow response exceeds the JSON frame limit")
                    async with write_lock:
                        writer.write(data)
                        # Pipe backpressure bounds buffering without imposing
                        # a deadline on a guest that is still computing.
                        await writer.drain()

                await send({"type": "start", "mode": mode, "source": source, "args": arguments, "limits": limits}, _MAX_START_BYTES)
                frames = _Frames(pipes.stdout, limits["frame_bytes"])
                semaphore = asyncio.Semaphore(limits["concurrency"]) if limits["concurrency"] is not None else None
                last_call_id = -1
                active_calls: set[str] = set()
                agent_count = 0
                callback_failure: BaseException | None = None

                async def handle_call(frame: dict) -> None:
                    nonlocal callback_failure
                    try:
                        try:
                            if on_call is None:
                                raise RuntimeFailure("invalid_protocol", "Validation cannot invoke workflow agents")
                            if frame["operation"] == "agent" and semaphore is not None:
                                async with semaphore:
                                    value = await on_call(frame["id"], frame["operation"], frame["arguments"])
                            else:
                                value = await on_call(frame["id"], frame["operation"], frame["arguments"])
                            if len(_encoded(value)) > limits["output_bytes"]:
                                raise RuntimeFailure("output_limit", "Workflow host-call result exceeds the limit")
                            response = {"type": "response", "id": frame["id"], "ok": True, "value": value}
                        except Exception as exc:
                            response = {"type": "response", "id": frame["id"], "ok": False, "error": _error(exc)}
                        except asyncio.CancelledError:
                            if closing or cancel_event.is_set():
                                raise
                            response = {"type": "response", "id": frame["id"], "ok": False, "error": {"code": "host_call_cancelled", "message": "Workflow host call was cancelled"}}
                        active_calls.discard(frame["id"])
                        await send(response)
                    except Exception as exc:
                        active_calls.discard(frame["id"])
                        callback_failure = exc
                        # Closing the pipe wakes the reader through worker EOF/error.
                        writer.close()
                    finally:
                        active_calls.discard(frame["id"])

                async def receive() -> dict:
                    nonlocal agent_count, last_call_id
                    while True:
                        try:
                            frame = await frames.read()
                        except Exception:
                            if callback_failure is not None:
                                raise callback_failure
                            raise
                        if callback_failure is not None:
                            raise callback_failure
                        kind = frame.get("type")
                        if kind == "call":
                            call_id = frame.get("id")
                            operation = frame.get("operation")
                            if mode != "run" or not isinstance(call_id, str) or not call_id.isdecimal() or len(call_id) > 80 or int(call_id) <= last_call_id or not isinstance(operation, str) or operation not in {"agent", "phase", "log"} or not isinstance(frame.get("arguments"), dict):
                                raise RuntimeFailure("invalid_protocol", "Invalid or duplicate workflow host call")
                            last_call_id = int(call_id)
                            if operation == "agent":
                                agent_count += 1
                                if limits["max_agents"] is not None and agent_count > limits["max_agents"]:
                                    raise RuntimeFailure("agent_limit", "Workflow agent limit exceeded")
                            task = asyncio.create_task(handle_call(frame))
                            tasks.add(task)
                            task.add_done_callback(tasks.discard)
                            active_calls.add(call_id)
                            continue
                        if active_calls:
                            raise RuntimeFailure("invalid_protocol", "Workflow terminated while host calls were active")
                        if kind == "validated" and mode == "validate" and frame.get("valid") is True:
                            return {"status": "completed", "result": None, "error": None}
                        if kind == "result" and mode == "run" and "result" in frame:
                            if len(_encoded(frame["result"])) > limits["output_bytes"]:
                                raise RuntimeFailure("output_limit", "Workflow result exceeds the per-value JSON safety bound")
                            return {"status": "completed", "result": frame["result"], "error": None}
                        if kind == "error" and isinstance(frame.get("error"), dict):
                            detail = frame["error"]
                            return {"status": "failed", "result": None, "error": {"code": str(detail.get("code", "script_error"))[:80], "message": str(detail.get("message", "Workflow failed"))[:2000]}}
                        raise RuntimeFailure("invalid_protocol", "Workflow worker emitted an unexpected frame")

                reader_task = asyncio.create_task(receive())
                cancel_task = asyncio.create_task(cancel_event.wait())
                # Compile-only validation has a transport watchdog. Executing
                # workflows have no deadline unless a manual run supplied one.
                duration = 5.0 if mode == "validate" else limits["timeout_s"]
                ready, _ = await asyncio.wait({reader_task, cancel_task}, timeout=duration, return_when=asyncio.FIRST_COMPLETED)
                if cancel_task in ready:
                    outcome = {"status": "cancelled", "result": None, "error": None}
                elif reader_task in ready:
                    outcome = reader_task.result()
                    exit_code = await asyncio.wait_for(process.wait(), 2)
                    if outcome["status"] == "completed" and exit_code != 0:
                        raise RuntimeFailure("worker_failed", "Workflow worker returned a result but exited unsuccessfully")
                else:
                    if mode == "validate":
                        raise RuntimeFailure("validation_timeout", "Workflow compiler did not answer the validation watchdog")
                    raise RuntimeFailure("timeout", "Workflow exceeded its explicitly configured wall-time limit")
            except asyncio.CancelledError:
                outcome = {"status": "cancelled", "result": None, "error": None}
                raise
            except Exception as exc:
                outcome = {"status": "failed", "result": None, "error": _error(exc)}
            finally:
                closing = True
                for task in (reader_task, cancel_task, *tasks):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(task for task in (reader_task, cancel_task, *tasks) if task is not None), return_exceptions=True)
                if process is not None:
                    try:
                        await process.terminate()
                    except Exception as exc:
                        outcome = {"status": "failed", "result": None, "error": {"code": "worker_cleanup_failed", "message": str(exc)[:2000]}}
        return outcome
