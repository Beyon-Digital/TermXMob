"""A fake interactive CLI for CLI-session tests: no real CLI, account or network involved.

It prints a banner and a ``> `` prompt, answers each typed line, and exposes a few probe
commands (``env``, ``hookurl``, ``approve``, ``secret``, ``pwd``, ``quit``, ``fail``). Set
``FAKE_MODE=stubborn`` to ignore SIGINT/SIGTERM so only SIGKILL ends it, and
``FAKE_ARGV_FILE`` to have it record the argv it was launched with.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

FAKE_SOURCE = r'''
import json, os, signal, sys

argv_file = os.environ.get("FAKE_ARGV_FILE")
if argv_file:
    with open(argv_file, "w") as handle:
        json.dump(sys.argv[1:], handle)
if os.environ.get("FAKE_MODE") == "stubborn":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, lambda *a: print("INTERRUPTED", flush=True))
print("FAKE READY", flush=True)
print("> ", end="", flush=True)
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line == "quit":
        sys.exit(0)
    if line == "fail":
        sys.exit(3)
    if line == "env":
        print("ENV:" + ",".join(sorted(os.environ)), flush=True)
    elif line == "hookurl":
        print("HOOKURL=" + os.environ.get("TERMX_CLI_HOOK_URL", ""), flush=True)
    elif line == "pwd":
        print("CWD=" + os.getcwd(), flush=True)
    elif line == "secret":
        print("leaked sk-abcdefghijklmnopqrstuvwxyz123456 and api_key=hunter2hunter2", flush=True)
    elif line == "approve":
        print("Do you want to proceed?\n 1. Yes\n 2. No, and tell the CLI what to do differently (esc)", flush=True)
        continue
    elif line == "burst":
        print("\n".join("line %d" % i for i in range(400)), flush=True)
    else:
        print("ECHO:" + line, flush=True)
    print("> ", end="", flush=True)
'''


def write_fake_cli(directory: Path, name: str = "fake-cli") -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{FAKE_SOURCE}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


posix_only = pytest.mark.skipif(os.name == "nt", reason="PTY signal ladder is POSIX-only")


async def wait_for(predicate, timeout: float = 8.0, step: float = 0.05):
    import asyncio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(step)
    raise AssertionError("condition not reached in time")


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True, capture_output=True)
    return path


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
