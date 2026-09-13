from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

import uvicorn

from termx.app import AppState, create_app
from termx.hostenv import augment_path
from termx.lifecycle import shutdown_state
from termx.net import connect_url, http_urls, qr_ascii
from termx import notify


def bundled_web_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    for candidate in (Path(base) / "web", Path(base) / "app" / "dist"):
        if candidate.is_dir():
            return candidate
    return None


def default_web_dir() -> Path:
    bundled = bundled_web_dir()
    if bundled is not None:
        return bundled
    cwd = Path.cwd()
    for candidate in (cwd / "app" / "dist", cwd.parent / "app" / "dist"):
        if candidate.is_dir():
            return candidate
    return cwd / "app" / "dist"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termx",
        description="Host interactive terminal sessions and connect from your phone or browser.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default 8787)")
    parser.add_argument("--passcode", default=None, help="Optional shared passcode")
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Open a Cloudflare quick tunnel (requires cloudflared)",
    )
    parser.add_argument(
        "--web-dir",
        default=None,
        help="Expo web export directory (default ./app/dist)",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Run as a desktop host: no banner, structured stdout events, local shutdown API",
    )
    return parser


def print_banner(urls: list[str], tunnel_url: str | None, passcode: str | None) -> None:
    def out(line: str = "") -> None:
        print(line, flush=True)

    out()
    out("termx")
    for url in urls:
        out(f"  local  {connect_url(url, passcode)}")
    if tunnel_url:
        out(f"  tunnel {connect_url(tunnel_url, passcode)}")
    if passcode:
        out(f"  passcode {passcode}")
    else:
        out("  passcode off")
    qr_target = connect_url(tunnel_url or (urls[-1] if urls else "http://127.0.0.1:8787"), passcode)
    out()
    out(qr_ascii(qr_target))
    out(f"  scan {qr_target}")
    out()


async def _wait_started(server: uvicorn.Server, task: "asyncio.Task[None]", timeout: float = 15.0) -> bool:
    waited = 0.0
    while not server.started:
        if task.done():
            return server.started
        if waited >= timeout:
            return server.started
        await asyncio.sleep(0.05)
        waited += 0.05
    return True


async def _start_server(
    app: object, host: str, port: int
) -> tuple[uvicorn.Server, "asyncio.Task[None]", int] | None:
    import socket

    candidates = [port] + [port + offset for offset in range(1, 10)] + [0]
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for candidate in candidates:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, candidate))
            sock.listen(2048)
            sock.set_inheritable(True)
        except OSError:
            sock.close()
            continue
        actual = int(sock.getsockname()[1])
        config = uvicorn.Config(
            app,
            host=host,
            port=actual,
            log_level="warning",
            ws="auto",
        )
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve(sockets=[sock]))
        if await _wait_started(server, serve_task):
            return server, serve_task, actual
        try:
            sock.close()
        except OSError:
            pass
        if not serve_task.done():
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass
    return None


async def _serve(args: argparse.Namespace) -> None:
    if args.desktop:
        os.environ["TERMX_DESKTOP"] = "1"
    web_dir = Path(args.web_dir) if args.web_dir else default_web_dir()
    state = AppState(passcode=args.passcode, port=args.port)
    app = create_app(state, web_dir=web_dir if web_dir.is_dir() else None)
    started = await _start_server(app, args.host, args.port)
    if started is None:
        if not args.desktop:
            print(f"termx: could not bind {args.host}:{args.port} (and nearby ports)", flush=True)
        return
    server, serve_task, actual_port = started
    if actual_port != args.port:
        state.port = actual_port
        state.tunnels.port = actual_port
    tunnel_url = None
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        server.should_exit = True

    state.request_shutdown = _request_shutdown
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
            loop.add_signal_handler(signal.SIGINT, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
        if args.tunnel:
            status = await state.tunnels.start_quick_cloudflare()
            tunnel_url = status.url
        if args.desktop:
            urls = http_urls(actual_port, args.host)
            notify.ready(actual_port, urls, tunnel_url)
            body = connect_url(tunnel_url or (urls[-1] if urls else f"http://127.0.0.1:{actual_port}"), args.passcode)
            notify.notify("Termx is running", body, kind="ready", url=tunnel_url or urls[0])
        else:
            print_banner(http_urls(actual_port, args.host), tunnel_url, args.passcode)
        await serve_task
    except (asyncio.CancelledError, KeyboardInterrupt):
        server.should_exit = True
        if not serve_task.done():
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.should_exit = True
        await shutdown_state(state)
        if not serve_task.done():
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass


def main() -> None:
    augment_path()
    args = build_parser().parse_args()
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass
