from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

from termx.app import AppState, create_app
from termx.hostenv import augment_path
from termx.lifecycle import shutdown_state
from termx.net import connect_url, http_urls, qr_ascii
from typing import Any
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
    for candidate in (
        cwd / "desktop" / "workspace" / "dist",
        cwd / "desktop" / "web",
        cwd.parent / "desktop" / "workspace" / "dist",
        cwd.parent / "desktop" / "web",
        cwd / "web",
    ):
        if candidate.is_dir():
            return candidate
    return cwd / "desktop" / "web"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="termx",
        description="Host interactive terminal sessions and connect from your phone or browser.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Private loopback bind address (default 127.0.0.1)")
    parser.add_argument('--lan-tls', action='store_true', help='Enable the separate HTTPS/WSS LAN listener (manual CA trust required)')
    parser.add_argument('--lan-host', default='0.0.0.0', help='HTTPS listener bind address')
    parser.add_argument('--lan-port', type=int, default=8788, help='HTTPS listener port (no fallback)')
    parser.add_argument('--lan-name', action='append', default=[], help='Additional resolving .local name or private IP SAN; repeatable')
    parser.add_argument('--lan-rotate-identity', action='store_true', help='Deliberately replace the LAN identity; every client must trust and pair again')
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default 8787)")
    parser.add_argument('--config-dir', default=None, help='Private host state profile directory')
    parser.add_argument('--service-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument("--passcode", default=None, help="Optional shared passcode")
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Open a Cloudflare quick tunnel (requires cloudflared)",
    )
    parser.add_argument(
        "--web-dir",
        default=None,
        help="Shared desktop workspace bundle directory (auto-detected by default)",
    )
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="Run as a desktop host: no banner, structured stdout events, local shutdown API",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--broker",
        default=None,
        help=argparse.SUPPRESS,
    )
    commands=parser.add_subparsers(dest='command')
    service=commands.add_parser('service', help='Manage an opt-in background user host')
    actions=service.add_subparsers(dest='service_action',required=True)
    for action in ('install','start','stop','status','uninstall'):
        control=actions.add_parser(action)
        control.add_argument('--config-dir',default=argparse.SUPPRESS)
        if action in {'install','start'}:
            control.add_argument('--host',default='127.0.0.1')
            control.add_argument('--port',type=int,default=8787)
            control.add_argument('--web-dir',default=argparse.SUPPRESS)
        if action=='install':
            control.add_argument('--tunnel-profile',default=None,help='Explicit existing tunnel profile to restore at service startup')
    return parser


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            ctypes.windll.kernel32.CloseHandle(process)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch_parent(parent_pid: int, request_shutdown: Any, interval: float = 2.0) -> None:
    """Exit the desktop sidecar when the app that launched it disappears."""

    def loop() -> None:
        while True:
            time.sleep(interval)
            if not pid_alive(parent_pid):
                request_shutdown()
                return

    threading.Thread(target=loop, name="termx-parent-watchdog", daemon=True).start()


def print_banner(urls: list[str], tunnel_url: str | None, passcode: str | None) -> None:
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'

    def out(line: str = "") -> None:
        # Redirected Windows output can use cp1252 even though the interactive
        # console supports Unicode. Display must not terminate a ready host.
        print(line.encode(encoding, errors='backslashreplace').decode(encoding), flush=True)

    out()
    out("termx")
    for url in urls:
        out(f"  local  {url}")
    if tunnel_url:
        out(f"  tunnel {tunnel_url}")
    if passcode:
        out("  passcode configured (redacted; sign in or pair from Settings)")
    else:
        out("  passcode off")
    qr_target = tunnel_url or (urls[-1] if urls else "http://127.0.0.1:8787")
    out()
    qr = qr_ascii(qr_target)
    try:
        qr.encode(encoding)
    except UnicodeEncodeError:
        out('  Open the connection link below.')
    else:
        out(qr)
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
    app: object, host: str, port: int, *, strict: bool = False, tls=None, lifespan="auto"
) -> tuple[uvicorn.Server, "asyncio.Task[None]", int] | None:
    import socket

    candidates = [port] if strict else [port] + [port + offset for offset in range(1, 10)] + [0]
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for candidate in candidates:
        sock = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if os.name == "nt":
            # On Windows SO_REUSEADDR allows hijacking a port another process
            # is using; exclusive use makes the bind fail instead.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
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
            access_log=False,
            proxy_headers=tls is None,
            forwarded_allow_ips=["127.0.0.1", "::1"],
            lifespan=lifespan,
        )
        if tls is not None:
            config.load()
            config.ssl = tls
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


async def _serve(args: argparse.Namespace) -> int:
    from termx.config import config_dir
    from termx.host_instance import HostInstance
    if getattr(args,'config_dir',None):
        os.environ['TERMX_CONFIG_DIR']=str(Path(args.config_dir).expanduser().resolve())
    if getattr(args,'service_worker',False):
        from termx.host_service import DISPLAY_KEYS, HostService
        settings=HostService()._settings()
        if not settings: raise ValueError('This host profile has no installed service configuration')
        args.host=settings['host'];args.port=settings['port'];args.web_dir=settings.get('web_dir')
        args.passcode=settings.get('bootstrap_passcode');args.desktop=False;args.parent_pid=None;args.broker=None
        args.tunnel_profile=settings.get('tunnel_profile_id');args.tunnel_authority=settings.get('tunnel_authority')
        for key,value in settings.get('display_environment',{}).items():
            if key in DISPLAY_KEYS:os.environ[key]=value
        os.environ['TERMX_DESKTOP']='0'
    instance=HostInstance(config_dir()).acquire()
    try:
        return await _serve_owned(args,instance)
    finally:
        instance.close()


async def _serve_owned(args: argparse.Namespace, instance) -> int:
    if args.desktop:
        os.environ["TERMX_DESKTOP"] = "1"
    from termx.desktop import broker as desktop_broker

    desktop_broker.configure(getattr(args, "broker", None))
    web_dir = Path(args.web_dir) if args.web_dir else default_web_dir()
    state = AppState(passcode=args.passcode, port=args.port)
    state.host_instance=instance
    app = create_app(state, web_dir=web_dir if web_dir.is_dir() else None)
    from termx.lan_tls import advertised_names, load_identity, ssl_context
    from termx.identity_http import loopback
    if not loopback(args.host):
        raise RuntimeError('The broker must bind to loopback. Use --lan-tls for HTTPS/WSS LAN access.')
    if getattr(args, 'lan_rotate_identity', False) and not getattr(args, 'lan_tls', False):
        raise RuntimeError('--lan-rotate-identity requires --lan-tls and deliberate client re-pairing')
    managed=getattr(args,'service_worker',False)
    lan_options = None
    saved_lan = instance.root / 'lan-tls-settings.json'
    if saved_lan.exists():
        from termx.host_instance import private_owned
        import json
        private_owned(saved_lan)
        from termx.lan_tls_http import LanSettings
        lan_options = LanSettings.model_validate_json(saved_lan.read_text()).model_dump()
    if getattr(args, 'lan_tls', False):
        lan_options = {'enabled': True, 'host': args.lan_host, 'port': args.lan_port, 'names': args.lan_name}
    if lan_options:
        from termx.lan_tls_http import LanSettings
        lan_options = LanSettings.model_validate(lan_options).model_dump()
    if lan_options and lan_options.get('enabled'):
        state.lan_identity = load_identity(instance.root, advertised_names(lan_options['host'], lan_options['names']), rotate=getattr(args, 'lan_rotate_identity', False))
        state.lan_port = lan_options['port']
    started = await _start_server(app, args.host, args.port, strict=managed)
    if started is None:
        if not args.desktop:
            suffix='' if managed else ' (and nearby ports)'
            print(f"termx: could not bind {args.host}:{args.port}{suffix}", flush=True)
        return 73 if managed else 0
    server, serve_task, actual_port = started
    lan_started = None
    renewal_task = None
    if getattr(state, 'lan_identity', None):
        lan_context = ssl_context(state.lan_identity)
        lan_started = await _start_server(app, lan_options['host'], lan_options['port'], strict=True, tls=lan_context, lifespan='off')
        if lan_started is None:
            server.should_exit = True
            await serve_task
            raise RuntimeError('LAN HTTPS could not bind. Choose an available --lan-port; no plaintext fallback was opened.')
        state.lan_port = lan_started[2]
        async def maintain_identity():
            while True:
                await asyncio.sleep(60)
                names = advertised_names(lan_options['host'], lan_options['names'])
                if state.lan_identity.renew(names):
                    lan_context.load_cert_chain(state.lan_identity.bundle)
        renewal_task = asyncio.create_task(maintain_identity())
    state.host_runtime=instance.publish(host=args.host,port=actual_port,managed=managed)
    if managed:
        from dataclasses import asdict
        from termx.desktop.capabilities import probe_desktop
        state.host_runtime['computer']=asdict(probe_desktop())
    if actual_port != args.port:
        state.port = actual_port
        state.tunnels.port = actual_port
    tunnel_url = None
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        server.should_exit = True
        if lan_started:
            lan_started[0].should_exit = True

    state.request_shutdown = _request_shutdown
    if args.parent_pid:
        watch_parent(args.parent_pid, _request_shutdown)
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
            loop.add_signal_handler(signal.SIGINT, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            pass
        selected=getattr(args,'tunnel_profile',None)
        if managed and selected:
            authority=getattr(args,'tunnel_authority',None)
            actor=state.identity.principal_by_id(authority['principal_id']) if authority else None
            permitted=state.identity.configured and (not authority or (actor and 'network-manage' in actor.scopes and 'host-admin' in actor.scopes and actor.policy_version==authority['policy_version']))
            profile=state.store.get_profile(selected) if permitted else None
            if profile:
                status=await state.tunnels.start(profile)
                tunnel_url=status.url if status.state=='connected' else None
                state.host_runtime['remote']={'state':status.state,'url':tunnel_url}
            else:
                state.host_runtime['remote']={'state':'blocked','url':None}
        elif args.tunnel:
            status = await state.tunnels.start_quick_cloudflare()
            tunnel_url = status.url
        if args.desktop:
            urls = [*http_urls(actual_port, args.host), *(state.lan_identity.metadata(state.lan_port)["urls"] if getattr(state,"lan_identity",None) else [])]
            notify.ready(actual_port, urls, tunnel_url)
            body = tunnel_url or (urls[-1] if urls else f"http://127.0.0.1:{actual_port}")
            notify.notify("Termx is running", body, kind="ready", url=tunnel_url or urls[0])
        elif not managed:
            print_banner([*http_urls(actual_port, args.host), *(state.lan_identity.metadata(state.lan_port)["urls"] if getattr(state,"lan_identity",None) else [])], tunnel_url, None if state.identity.configured else args.passcode)
        pending = [serve_task, *([lan_started[1], renewal_task] if lan_started else [])]
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except (asyncio.CancelledError, KeyboardInterrupt):
        server.should_exit = True
        if not serve_task.done():
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        server.should_exit = True
        if renewal_task:
            renewal_task.cancel()
            await asyncio.gather(renewal_task, return_exceptions=True)
        if lan_started:
            lan_started[0].should_exit = True
            await lan_started[1]
        await shutdown_state(state)
        if not serve_task.done():
            serve_task.cancel()
            try:
                await serve_task
            except (asyncio.CancelledError, Exception):
                pass

    # This exact status is consumed only by the desktop parent. It preserves
    # deliberate shutdown even if its stdout reader loses the exit race.
    return notify.HOST_STOP_EXIT_CODE if args.desktop and state.host_stop_requested else 0


def main() -> None:
    augment_path()
    args = build_parser().parse_args()
    if args.command == 'service':
        import json
        from termx.host_service import HostService
        controller=HostService(getattr(args,'config_dir',None))
        try:
            action=args.service_action
            if action=='install':
                result=controller.install(host=args.host,port=args.port,web_dir=args.web_dir or str(default_web_dir()),
                    bootstrap_passcode=os.environ.get('TERMX_SERVICE_PASSCODE'),tunnel_profile_id=args.tunnel_profile)
            elif action=='start':result=controller.start(bootstrap_passcode=os.environ.get('TERMX_SERVICE_PASSCODE'))
            else:result=getattr(controller,action)()
            print(json.dumps(result),flush=True)
        except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
            print(json.dumps({'ok':False,'error':str(exc)}),flush=True)
            raise SystemExit(1) from None
        return
    try:
        status = asyncio.run(_serve(args))
        if status:
            raise SystemExit(status)
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ValueError, PermissionError) as exc:
        print(str(exc),file=sys.stderr,flush=True)
        raise SystemExit(73) from None
