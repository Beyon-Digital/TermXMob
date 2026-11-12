"""Standalone constrained JavaScript worker; stdout is bounded JSON-lines only.

The interpreter has no host callbacks, module loader, or libc bindings. OS
containment is supplied separately by the parent's restricted SandboxRunner.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

MAX_FRAME_BYTES = 1_048_576
MAX_SOURCE_BYTES = 65_536
MAX_ARGS_BYTES = 65_536


class WorkerFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _emit(value: dict, output_limit: int = MAX_FRAME_BYTES) -> None:
    data = (_encode(value) + "\n").encode()
    if len(data) > min(output_limit, MAX_FRAME_BYTES):
        raise WorkerFailure("output_limit", "Workflow output exceeds the JSON limit")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _read() -> dict:
    data = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
    if not data:
        raise WorkerFailure("broken_ipc", "Workflow parent closed the input pipe")
    if len(data) > MAX_FRAME_BYTES or not data.endswith(b"\n"):
        raise WorkerFailure("frame_limit", "Workflow input frame exceeds the limit")
    try:
        item = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WorkerFailure("invalid_protocol", "Workflow input is not valid JSON") from exc
    if not isinstance(item, dict):
        raise WorkerFailure("invalid_protocol", "Workflow input must be an object")
    return item


def _number(configuration: dict, key: str, default: float, minimum: float, maximum: float) -> float:
    value = configuration.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WorkerFailure("invalid_limits", f"Invalid workflow limit: {key}")
    return min(maximum, max(minimum, value))


def _quota(configuration: dict, key: str) -> int | float | None:
    value = configuration.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)) or value <= 0:
        raise WorkerFailure("invalid_limits", f"Invalid workflow quota: {key}")
    if key != "timeout_s" and value != int(value):
        raise WorkerFailure("invalid_limits", f"Workflow quota {key} must be an integer")
    return value if key == "timeout_s" else int(value)


def _main() -> None:
    request = _read()
    if request.get("type") != "start" or request.get("mode") not in {"validate", "run"}:
        raise WorkerFailure("invalid_protocol", "Expected a workflow start frame")
    source = request.get("source")
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > MAX_SOURCE_BYTES:
        raise WorkerFailure("invalid_source", "Workflow source must contain 1..65536 UTF-8 bytes")
    arguments = request.get("args", {})
    encoded_args = _encode(arguments)
    if len(encoded_args.encode()) > MAX_ARGS_BYTES:
        raise WorkerFailure("argument_limit", "Workflow arguments exceed the JSON limit")
    configuration = request.get("limits", {})
    if not isinstance(configuration, dict):
        raise WorkerFailure("invalid_limits", "Workflow limits must be an object")
    limits = {
        "heap_bytes": int(_number(configuration, "heap_bytes", 32 << 20, 1 << 20, 64 << 20)),
        "stack_bytes": int(_number(configuration, "stack_bytes", 256 << 10, 64 << 10, 512 << 10)),
        "max_agents": _quota(configuration, "max_agents"),
        "frame_bytes": int(_number(configuration, "frame_bytes", 524_288, 1024, MAX_FRAME_BYTES)),
        "output_bytes": int(_number(configuration, "output_bytes", 262_144, 1024, 524_288)),
    }
    try:
        import quickjs
    except ImportError as exc:
        raise WorkerFailure("runtime_unavailable", "JavaScript workflows are not installed on this host") from exc

    context = quickjs.Context()
    context.set_memory_limit(limits["heap_bytes"])
    context.set_max_stack_size(limits["stack_bytes"])
    bootstrap = Path(__file__).with_name("bridge.js").read_text(encoding="utf-8")
    context.eval("globalThis.__workflow_controller = " + bootstrap)
    compile_source = context.eval("__workflow_controller.compile")
    start = context.eval("__workflow_controller.start")
    settle = context.eval("__workflow_controller.settle")
    poll = context.eval("__workflow_controller.poll")
    context.eval("delete globalThis.__workflow_controller")
    try:
        compile_source(source, _encode(limits))
    except quickjs.JSException as exc:
        raise WorkerFailure("invalid_source", str(exc)[:2000]) from exc
    if request["mode"] == "validate":
        _emit({"type": "validated", "valid": True})
        return
    start(encoded_args)
    while True:
        while context.execute_pending_job():
            pass
        state = json.loads(poll())
        for call in state["requests"]:
            _emit(call, limits["frame_bytes"])
        if state.get("outcome") is not None:
            _emit(state["outcome"], min(limits["output_bytes"], limits["frame_bytes"]))
            return
        if not state["pending"]:
            raise WorkerFailure("no_progress", "Workflow is waiting on a promise without an active agent or event")
        response = _read()
        if response.get("type") != "response" or not isinstance(response.get("id"), str) or not isinstance(response.get("ok"), bool):
            raise WorkerFailure("invalid_protocol", "Expected a workflow response frame")
        settle(_encode(response))


def main() -> int:
    try:
        _main()
        return 0
    except BaseException as exc:
        code = exc.code if isinstance(exc, WorkerFailure) else "script_error"
        message = str(exc)[:2000] or type(exc).__name__
        if code == "script_error":
            if isinstance(exc, MemoryError) or message.startswith("InternalError: out of memory"):
                code = "heap_limit"
            elif message.startswith("InternalError: stack overflow"):
                code = "stack_limit"
        try:
            _emit({"type": "error", "error": {"code": code, "message": message}})
        except BaseException:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
