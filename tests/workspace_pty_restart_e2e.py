"""Terminals and CLI sessions across real host restarts: real ``termx`` processes, real sockets, no browser.

A host is started in held mode, a shell terminal and a (fake, hook-capable) CLI session are created, then the
host process is stopped (SIGTERM) and later killed hard (SIGKILL). Each time a NEW host process is started over
the same config directory and must re-attach: same terminal, same shell process, replay intact including output
produced while no host was running, and the CLI session's own hook still reaching the new host.
Run: ``python tests/workspace_pty_restart_e2e.py [report-dir]`` (POSIX only; takes about a minute).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic, sleep

import httpx
import websockets

HOOKY_CLI = r'''
import json, os, subprocess, sys

args = sys.argv[1:]
settings = json.load(open(args[args.index("--settings") + 1])) if "--settings" in args else {"hooks": {}}


def fire(event, **extra):
    entries = settings["hooks"].get(event)
    if not entries:
        return
    command = entries[0]["hooks"][0]["command"]
    payload = json.dumps({"hook_event_name": event, "session_id": "native", **extra}).encode()
    subprocess.run(command, shell=True, input=payload, env=os.environ, capture_output=True, timeout=20)


fire("SessionStart")
print("HOOKY READY", flush=True)
for raw in sys.stdin:
    line = raw.rstrip("\n")
    if line == "turn":
        fire("UserPromptSubmit")
        print("TURN RUNNING", flush=True)
        fire("Stop")
    elif line == "ask":
        fire("PermissionRequest", tool_name="Bash")
        print("ASKING", flush=True)
    else:
        print("ECHO:" + line, flush=True)
'''

PASSCODE = "restart-e2e-passcode"
HEADERS = {"X-Termx-Passcode": PASSCODE}


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def eventually(check, timeout=20.0, label="condition"):
    deadline = monotonic() + timeout
    last = None
    while monotonic() < deadline:
        try:
            last = check()
        except (httpx.HTTPError, OSError, KeyError, AssertionError) as exc:
            last = exc
            sleep(0.2)
            continue
        if last:
            return last
        sleep(0.2)
    raise AssertionError(f"{label} not reached (last: {last})")


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


class Host:
    def __init__(self, root, port, env):
        self.root, self.port, self.env = root, port, env
        self.process = None
        self.base = f"http://127.0.0.1:{port}"

    def start(self):
        log = open(self.root / f"host-{self.port}-{monotonic():.0f}.log", "wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "termx", "--host", "127.0.0.1", "--port", str(self.port), "--passcode", PASSCODE],
            env=self.env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        eventually(lambda: httpx.post(self.base + "/graphql", json={"query": "{ health { ok } }"}, timeout=2).json()["data"]["health"]["ok"],
                   timeout=40, label="host health")
        return self

    def stop(self, sig):
        self.process.send_signal(sig)
        self.process.wait(timeout=40)

    def graphql(self, query, variables=None):
        reply = httpx.post(self.base + "/graphql", json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=20).json()
        assert "errors" not in reply, reply
        return reply["data"]

    def status(self):
        return self.graphql("""{ host_status { holder { mode reason running pid sessions direct_terminals }
            recovery { live_cli_sessions lost_cli_sessions } heartbeat { at age_s } keep_awake { enabled } } }""")["host_status"]

    async def terminal(self, pty_id, send=None, wait_for=None, timeout=15):
        async with websockets.connect(f"ws://127.0.0.1:{self.port}/api/sessions/{pty_id}/pty", additional_headers=HEADERS) as socket_:
            seen = b""
            if send:
                await socket_.send(send)
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                if wait_for and all(re.search(item, seen.decode("utf-8", "replace")) for item in wait_for):
                    break
                try:
                    message = await asyncio.wait_for(socket_.recv(), 1.5)
                    if isinstance(message, bytes):  # text frames are control events (cwd, activity), not terminal output
                        seen += message
                except asyncio.TimeoutError:
                    if not wait_for:
                        break
                    continue
            return seen.decode("utf-8", "replace")


def run(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = {"proof": "pty-holder-survives-host-restart", "steps": []}

    def step(name, **facts):
        report["steps"].append({"step": name, **facts})
        print("ok", name, flush=True)

    with tempfile.TemporaryDirectory(prefix="termx-restart-e2e-") as temporary:
        root = Path(temporary)
        config = root / "config"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        cli = bin_dir / "claude"
        cli.write_text(f"#!{sys.executable}\n{HOOKY_CLI}", encoding="utf-8")
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        project = root / "project"
        project.mkdir()
        env = {**os.environ, "TERMX_CONFIG_DIR": str(config), "TERMX_AGENTS_DIR": str(root / "agents"), "TERMX_PTY_HOLDER": "held",
               "TERMX_PTY_HOLDER_IDLE_S": "300", "TERMX_ENGINE_STARTUP_REFRESH": "0", "TERMX_PROVIDER_STARTUP_REFRESH": "0",
               "TERMX_AGENT_DESKTOP_STARTUP_REFRESH": "0", "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "SHELL": "/bin/bash"}
        port = free_port()
        holder_pid = None
        try:
            host = Host(root, port, env).start()
            before = host.status()
            assert before["holder"]["mode"] == "held" and before["holder"]["reason"] is None, before
            step("host A is up in held mode", holder=before["holder"]["mode"])

            made = host.graphql(
                "mutation($i: CreateSessionInput) { create_session(input: $i) { id title } }",
                {"i": {"title": "e2e shell", "cwd": str(project), "shell": "/bin/bash"}},
            )["create_session"]
            pty_id = made["id"]
            text = asyncio.run(host.terminal(
                pty_id, b"echo SURVIVE-$((20+22)); echo SHELLPID=$$; (sleep 9; echo LATE-OUTPUT) &\n", [r"SURVIVE-42\r?\n", r"SHELLPID=\d+"]))
            shell_pid = int(re.search(r"SHELLPID=(\d+)", text).group(1))
            cli_made = httpx.post(host.base + "/api/cli-sessions", headers=HEADERS, json={"cli": "claude", "cwd": str(project)}, timeout=30)
            assert cli_made.status_code == 201, cli_made.text
            cli_row = cli_made.json()
            eventually(lambda: httpx.get(f"{host.base}/api/cli-sessions/{cli_row['id']}", headers=HEADERS).json()["status"] in ("waiting_input", "idle"),
                       label="CLI session ready")
            status = host.status()
            holder_pid = status["holder"]["pid"]
            assert status["holder"]["sessions"] == 2 and status["holder"]["direct_terminals"] == 0, status
            assert alive(shell_pid) and alive(holder_pid)
            assert (config / "heartbeat.json").is_file()
            step("terminal and CLI session created", shell_pid=shell_pid, holder_pid=holder_pid, held=status["holder"]["sessions"])

            # --- round 1: a graceful stop, then a new host over the same config -------------------
            host.stop(signal.SIGTERM)
            assert alive(shell_pid) and alive(holder_pid), "stopping the host must not end the terminals"
            step("host A stopped with SIGTERM; terminals still running", shell_alive=True)
            # The CLI's hook URL names the host's port, so a restarted host must listen on the same port (a service does).
            sleep(8)  # the shell's background job prints into the pty while no host is attached
            host = Host(root, port, env).start()
            ids = {item["id"] for item in host.graphql("{ sessions { id exited } }")["sessions"]}
            assert pty_id in ids, ids
            replay = asyncio.run(host.terminal(pty_id, b"echo SHELLPID2=$$\n", [r"SURVIVE-42\r?\n", r"LATE-OUTPUT\r?\n", r"SHELLPID2=\d+"]))
            assert int(re.search(r"SHELLPID2=(\d+)", replay).group(1)) == shell_pid, "the same shell process survived"
            late = re.search(r"LATE-OUTPUT\r?\n", replay)
            assert late, repr(replay[-600:])
            assert re.search(r"SURVIVE-42\r?\n", replay).start() < late.start(), "replay is in order and includes output produced while no host ran"
            row = httpx.get(f"{host.base}/api/cli-sessions/{cli_row['id']}", headers=HEADERS).json()
            assert row["status"] not in ("lost", "done", "failed") and row["pty_session_id"] == cli_row["pty_session_id"], row
            post = host.status()
            assert post["holder"]["pid"] == holder_pid and post["recovery"]["live_cli_sessions"] == 1 and post["recovery"]["lost_cli_sessions"] == 0, post
            step("host B re-attached the terminal and the CLI session", replay_bytes=len(replay), cli_status=row["status"])

            # the CLI's own hook, issued before the restart, still reaches the new host
            sent = httpx.post(f"{host.base}/api/cli-sessions/{cli_row['id']}/input", headers=HEADERS, json={"text": "ask"}, timeout=20)
            assert sent.status_code == 200, sent.text
            hooked = eventually(lambda: (lambda r: r if r["status"] == "needs_approval" and r["status_source"] == "hook" else None)(
                httpx.get(f"{host.base}/api/cli-sessions/{cli_row['id']}", headers=HEADERS).json()), label="hook status after restart")
            step("a hook from the surviving CLI reached the new host", status=hooked["status"], source=hooked["status_source"])

            # --- round 2: a hard crash (no shutdown path runs) ----------------------------------------
            asyncio.run(host.terminal(pty_id, b"(sleep 3; echo AFTER-CRASH-OUTPUT) &\n", None, timeout=2))
            host.stop(signal.SIGKILL)
            assert alive(shell_pid) and alive(holder_pid), "a killed host must not take the terminals with it"
            sleep(4)
            host = Host(root, port, env).start()
            replay = asyncio.run(host.terminal(pty_id, b"echo SHELLPID3=$$\n", [r"SURVIVE-42\r?\n", r"AFTER-CRASH-OUTPUT\r?\n", r"SHELLPID3=\d+"]))
            assert int(re.search(r"SHELLPID3=(\d+)", replay).group(1)) == shell_pid
            final = host.status()
            assert final["holder"]["pid"] == holder_pid and final["holder"]["sessions"] == 2 and final["recovery"]["lost_cli_sessions"] == 0, final
            step("host C re-attached after SIGKILL; replay intact", sessions=final["holder"]["sessions"])

            # --- explicit teardown: the only thing that ends held terminals ----------------------------
            host.stop(signal.SIGTERM)
            stopped = subprocess.run([sys.executable, "-m", "termx", "pty-holder", "stop", "--kill", "--config-dir", str(config)],
                                     env=env, capture_output=True, text=True, timeout=60)
            assert '"stopped": true' in stopped.stdout, stopped.stdout + stopped.stderr
            eventually(lambda: not alive(holder_pid) and not alive(shell_pid), label="holder and shell gone after pty-holder stop --kill")
            step("explicit holder shutdown ended the terminals")
            report["result"] = "passed"
        finally:
            for pid in (holder_pid,):
                if pid and alive(pid):
                    os.kill(pid, signal.SIGKILL)
            report.setdefault("result", "failed")
            (destination / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="termx-restart-report-")))
