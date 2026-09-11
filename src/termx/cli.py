from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

import uvicorn

from termx.app import AppState, create_app
from termx.lifecycle import shutdown_state
from termx.net import connect_url, http_urls, qr_ascii


def default_web_dir() -> Path:
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


async def _serve(args: argparse.Namespace) -> None:
    web_dir = Path(args.web_dir) if args.web_dir else default_web_dir()
    state = AppState(passcode=args.passcode, port=args.port)
    app = create_app(state, web_dir=web_dir if web_dir.is_dir() else None)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        ws="auto",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    tunnel_url = None
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        server.should_exit = True

    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
            loop.add_signal_handler(signal.SIGINT, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
        await asyncio.sleep(0.25)
        if args.tunnel:
            status = await state.tunnels.start_quick_cloudflare()
            tunnel_url = status.url
        print_banner(http_urls(args.port, args.host), tunnel_url, args.passcode)
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
    args = build_parser().parse_args()
    try:
        asyncio.run(_serve(args))
    except KeyboardInterrupt:
        pass
