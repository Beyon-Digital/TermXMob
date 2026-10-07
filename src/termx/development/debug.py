"""Host-owned DAP sessions; UI reconnect never launches a second debuggee.

Python uses debugpy stdio; JavaScript/TypeScript uses Microsoft's standalone
js-debug DAP server bound to loopback. Neither adapter endpoint is exposed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid
from collections import deque
from pathlib import Path

from fastapi import HTTPException
from termx.agent.execution import SENSITIVE_ENV

MAX_FRAME = 4 * 1024 * 1024
COMMANDS = frozenset({"initialize", "launch", "attach", "setBreakpoints", "setExceptionBreakpoints",
    "configurationDone", "threads", "stackTrace", "scopes", "variables", "evaluate", "continue",
    "next", "stepIn", "stepOut", "pause", "disconnect", "terminate", "source", "loadedSources"})


async def read_frame(reader):
    size = None
    while True:
        line = await reader.readline()
        if not line:
            raise EOFError
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.partition(b":")
        if key.lower() == b"content-length":
            size = int(value)
    if size is None or not 0 <= size <= MAX_FRAME:
        raise ValueError("Invalid DAP frame")
    return json.loads(await reader.readexactly(size))


async def write_frame(writer, value):
    body = json.dumps(value).encode()
    if len(body) > MAX_FRAME:
        raise ValueError("DAP frame exceeds limit")
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await writer.drain()


class DapConnection:
    def __init__(self, session, reader, writer):
        self.session, self.reader, self.writer = session, reader, writer
        self.seq = 0
        self.pending = {}
        self.task = asyncio.create_task(self.read())

    async def request(self, command, arguments):
        if command == 'launch' and '__pendingTargetId' not in arguments:
            self.session.launch_arguments = dict(arguments)
        if command == "setBreakpoints":
            self.session.breakpoints[arguments.get("source", {}).get("path", "")] = arguments
        elif command == "setExceptionBreakpoints":
            self.session.exceptions = arguments
        self.seq += 1
        seq = self.seq
        future = asyncio.get_running_loop().create_future()
        self.pending[seq] = future
        try:
            await write_frame(self.writer, {"seq": seq, "type": "request", "command": command, "arguments": arguments})
            result = await asyncio.wait_for(future, 45)
            if not result.get("success"):
                raise HTTPException(409, result.get("message", "Debug adapter rejected command"))
            return result.get("body", {})
        finally:
            self.pending.pop(seq, None)

    async def read(self):
        try:
            while True:
                message = await read_frame(self.reader)
                if message.get("type") == "response":
                    future = self.pending.get(message.get("request_seq"))
                    if future and not future.done():
                        future.set_result(message)
                elif message.get("type") == "event":
                    self.session.event(message)
                    if message.get("event") == "stopped":
                        self.session.active = self
                elif message.get("type") == "request":
                    asyncio.create_task(self.reverse(message))
        except (EOFError, OSError, ValueError, asyncio.IncompleteReadError) as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Debug adapter disconnected"))
            self.session.event({"type": "event", "event": "adapterClosed", "body": {"reason": type(exc).__name__}})

    async def reverse(self, message):
        success = False
        if message.get("command") == "startDebugging" and self.session.language != "python":
            config = message.get("arguments", {}).get("configuration", {})
            if config.get("__pendingTargetId"):
                await self.session.child(config, message.get("arguments", {}).get("request", "launch"))
                success = True
        await write_frame(self.writer, {"seq": 0, "type": "response", "request_seq": message["seq"],
            "command": message["command"], "success": success,
            "message": "Client-side arbitrary process launch is disabled" if not success else ""})

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        if hasattr(self.writer, "close"):
            self.writer.close()


class DebugSession:
    def __init__(self, project_id, root, language):
        self.id = uuid.uuid4().hex
        self.project_id, self.root, self.language = project_id, Path(root).resolve(), language
        self.workspace_session = self.worktree_id = self.worktree_digest = None
        self.attached = False
        self.events = deque(maxlen=2000)
        self.cursor = 0
        self.process = None
        self.connections = []
        self.active = None
        self.port = None
        self.breakpoints = {}
        self.exceptions = None
        self.launch_arguments = {}
        self.status = "starting"
        self.initialize = {"clientID": "termx", "adapterID": language, "pathFormat": "path",
                           "linesStartAt1": True, "columnsStartAt1": True,
                           "supportsRunInTerminalRequest": False, "supportsStartDebuggingRequest": True}

    def event(self, message):
        self.cursor += 1
        self.events.append({"id": self.cursor, **message})
        if message.get("event") == "terminated":
            self.status = "terminated"

    async def start(self, command):
        env = {k: v for k, v in os.environ.items() if not SENSITIVE_ENV.search(k)}
        self.process = await asyncio.create_subprocess_exec(*command, cwd=self.root, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt")
        if self.language == "python":
            reader, writer = self.process.stdout, self.process.stdin
        else:
            line = await asyncio.wait_for(self.process.stdout.readline(), 10)
            match = re.search(rb"127\.0\.0\.1:(\d+)", line)
            if not match:
                await self.close()
                raise HTTPException(503, "JavaScript debug adapter did not bind loopback")
            self.port = int(match[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.active = DapConnection(self, reader, writer)
        self.connections.append(self.active)
        asyncio.create_task(self.drain_stderr())
        self.status = "ready"

    async def drain_stderr(self):
        while self.process and await self.process.stderr.read(8192):
            pass

    async def child(self, config, request):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        connection = DapConnection(self, reader, writer)
        self.connections.append(connection)
        await connection.request("initialize", self.initialize)
        # The standalone adapter's reverse request carries only a target ID.
        # Preserve the host-validated source-map/workspace configuration on the
        # delegated target, rather than applying its default non-pausing config.
        launch = asyncio.create_task(connection.request(request, {**self.launch_arguments, **config}))
        for arguments in list(self.breakpoints.values()):
            await connection.request("setBreakpoints", arguments)
        if self.exceptions is not None:
            await connection.request("setExceptionBreakpoints", self.exceptions)
        await connection.request("configurationDone", {})
        await launch
        self.active = connection

    def scoped_path(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise HTTPException(403, "Debug source must stay inside the project")
        return str(path)

    def validate(self, command, arguments):
        if command not in COMMANDS:
            raise HTTPException(400, "Unsupported debug command")
        args = dict(arguments)
        if command == "initialize":
            args = {**self.initialize, **{k: v for k, v in args.items() if k in self.initialize}}
        if command == "launch":
            allowed = {"program", "args", "stopOnEntry", "justMyCode", "outFiles", "sourceMaps"}
            if set(args) - allowed or not isinstance(args.get("program"), str):
                raise HTTPException(400, "Launch requires a project program and supported options")
            args.update(program=self.scoped_path(args["program"]), cwd=str(self.root), console="internalConsole")
            if self.language == "python":
                from termx.desktop.runtime import python_interpreter
                interpreter = python_interpreter()
                if not interpreter:
                    raise HTTPException(503, "Python debugging requires a Python interpreter installed on the host")
                args.update(python=interpreter, type="python")
            else:
                from termx.desktop.runtime import node_binary
                args.update(type="pwa-node", runtimeExecutable=node_binary(), autoAttachChildProcesses=False,
                            pauseForSourceMap=True, rootPath=str(self.root),
                            resolveSourceMapLocations=[str(self.root / '**'), '!**/node_modules/**'])
                args["outFiles"] = [str(self.root / "**/*.js")]
                if self.language == 'typescript':
                    args['runtimeSourcemapPausePatterns'] = [str(self.root / '**/*.js')]
            args["env"] = {k: v for k, v in os.environ.items() if not SENSITIVE_ENV.search(k)}
        if command == "attach":
            # Attach can inspect an existing host process, which is a host-admin
            # operation enforced separately by the HTTP router.
            connect = args.get("connect", {})
            if set(args) - {"connect", "port"} or connect.get("host", "127.0.0.1") != "127.0.0.1":
                raise HTTPException(403, "Only explicitly authorized loopback attach is supported")
            port = connect.get("port", args.get("port"))
            if not isinstance(port, int) or not 1024 <= port <= 65535:
                raise HTTPException(400, "Invalid debug target port")
            args = {"connect": {"host": "127.0.0.1", "port": port}} if self.language == "python" else {"port": port, "address": "127.0.0.1", "type": "pwa-node"}
        if command == "setBreakpoints":
            args["source"] = {"path": self.scoped_path(args.get("source", {}).get("path", ""))}
        if command == "disconnect":
            args["terminateDebuggee"] = not self.attached
        return args

    async def request(self, command, arguments):
        if command == 'attach' and self.language == 'python':
            # A debugpy --listen target already owns its DAP adapter. A second
            # stdio adapter cannot attach to that client-facing listener.
            port = arguments['connect']['port']
            reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), 5)
            connection = DapConnection(self, reader, writer)
            try:
                await connection.request('initialize', self.initialize)
            except BaseException:
                await connection.close()
                raise
            self.connections.append(connection)
            self.active = connection
            self.attached = True
            return await connection.request('attach', {'justMyCode': True})
        if command == 'attach':
            self.attached = True
        return await self.active.request(command, arguments)

    async def close(self):
        for connection in self.connections:
            if not connection.task.done():
                try:
                    await asyncio.wait_for(connection.request('disconnect', {'terminateDebuggee': not self.attached}), 3)
                except (Exception, asyncio.CancelledError):
                    pass
        for connection in self.connections:
            await connection.close()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.status = "terminated"


class DebugService:
    def __init__(self):
        self.sessions = {}

    def commands(self):
        from termx.desktop.runtime import python_adapter, node_binary, debug_server
        python = python_adapter()
        server, node = debug_server(), node_binary()
        javascript = [node, server, "0", "127.0.0.1"] if node and server else None
        return {"python": python, "javascript": javascript, "typescript": javascript}

    def capabilities(self):
        return {lang: {"available": bool(command), "adapter": "debugpy" if lang == "python" else "microsoft-js-debug",
            "install": "Install the development extra" if lang == "python" else "Install pinned js-debug DAP artifact; set TERMX_JS_DEBUG_SERVER"}
            for lang, command in self.commands().items()}

    async def create(self, project_id, root, language):
        command = self.commands().get(language)
        if not command:
            raise HTTPException(503, "Debug adapter unavailable; install it on the host")
        session = DebugSession(project_id, root, language)
        await session.start(command)
        self.sessions[session.id] = session
        return session

    async def close(self):
        for session in self.sessions.values():
            await session.close()
