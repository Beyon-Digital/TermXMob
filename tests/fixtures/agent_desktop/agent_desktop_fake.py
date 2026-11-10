"""A stand-in for the agent-desktop CLI used by the test suite.

Standard library only. It never reads the environment for its scenario (the
client passes an allow-listed environment); it reads ``scenario.json`` that sits
beside it and appends one line per call to ``calls.jsonl`` there. The tests run
it through the client's ``launcher`` seam with the interpreter that runs pytest,
so no shebang, executable bit or ``.cmd`` wrapper is needed on any host.

Scenario file: a list of rules ``{"match": {...}, "respond": {...}}``. The first
rule that matches wins. ``match`` keys: ``command``, ``argv_contains`` (all must
appear), ``argv_regex`` (searched in the space-joined argv). ``respond`` keys:
``envelope`` (printed as JSON), ``stdout_raw``, ``stdout_bytes`` (N filler
bytes), ``exit``, ``delay_ms``, ``stderr``, ``sequence`` (a list of respond
dicts consumed one per matching call; the last one repeats).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    argv = sys.argv[1:]
    stdin_text = None
    if "--stdin" in argv:
        stdin_text = sys.stdin.buffer.read().decode("utf-8", "replace")
    env_seen = {
        "AGENT_DESKTOP_HOME": os.environ.get("AGENT_DESKTOP_HOME"),
        "AGENT_DESKTOP_MACOS_HELPER_PATH": os.environ.get("AGENT_DESKTOP_MACOS_HELPER_PATH"),
        "PATH": bool(os.environ.get("PATH")),
        "TERMX_SECRET": os.environ.get("TERMX_SECRET"),
        "RUST_LOG": os.environ.get("RUST_LOG"),
    }
    with (HERE / "calls.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "stdin": stdin_text, "cwd": os.getcwd(), "env": env_seen, "pid": os.getpid()}) + "\n")
    scenario_path = HERE / "scenario.json"
    rules = json.loads(scenario_path.read_text("utf-8")) if scenario_path.exists() else []
    command = argv[0] if argv else ""
    joined = " ".join(argv)
    for index, rule in enumerate(rules):
        match = rule.get("match") or {}
        if match.get("command") not in (None, command):
            continue
        if any(item not in argv for item in match.get("argv_contains") or []):
            continue
        if match.get("argv_regex") and not re.search(match["argv_regex"], joined):
            continue
        return respond(index, rule.get("respond") or {})
    print(json.dumps({"version": "2.6", "ok": False, "command": command or "unknown",
                      "error": {"code": "INVALID_ARGS", "message": "no scenario rule",
                                "disposition": {"delivery": "not_delivered", "retry": "safe"}}}))
    return 1


def respond(index: int, spec: dict) -> int:
    if "sequence" in spec:
        counters_path = HERE / "counters.json"
        counters = json.loads(counters_path.read_text("utf-8")) if counters_path.exists() else {}
        seen = int(counters.get(str(index), 0))
        counters[str(index)] = seen + 1
        counters_path.write_text(json.dumps(counters), "utf-8")
        sequence = spec["sequence"]
        spec = sequence[min(seen, len(sequence) - 1)]
    if spec.get("delay_ms"):
        time.sleep(spec["delay_ms"] / 1000)
    if spec.get("stderr"):
        sys.stderr.write(spec["stderr"])
    if "stdout_bytes" in spec:
        sys.stdout.write("x" * int(spec["stdout_bytes"]))
    elif "stdout_raw" in spec:
        sys.stdout.write(spec["stdout_raw"])
    elif "envelope" in spec:
        sys.stdout.write(json.dumps(spec["envelope"]))
    sys.stdout.flush()
    return int(spec.get("exit", 0 if (spec.get("envelope") or {}).get("ok", True) else 1))


if __name__ == "__main__":
    raise SystemExit(main())
