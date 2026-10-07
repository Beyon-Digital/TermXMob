"""Execute packaged workspace runtimes without host credentials or providers."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile

from termx.desktop import runtime
from termx.lsp import _read_message, _write_message


async def _response(process, identifier: int, *, dap: bool = False):
    async with asyncio.timeout(30):
        while True:
            message = json.loads(await _read_message(process))
            if dap and message.get('type') == 'response' and message.get('request_seq') == identifier:
                if not message.get('success'):
                    raise RuntimeError('Packaged debug adapter rejected initialization')
                return message.get('body', {})
            if not dap and message.get('id') == identifier:
                if 'error' in message:
                    raise RuntimeError('Packaged language server rejected initialization')
                return message.get('result')


async def _stop(process):
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def _command(argv: list[str], timeout: int = 30) -> str:
    process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise RuntimeError('Packaged runtime exited unsuccessfully: ' + Path(argv[0]).name)
        return (output or error).decode('utf-8', 'replace').splitlines()[0][:160]
    finally:
        await _stop(process)


async def _language(name: str, folder: Path) -> dict:
    command = runtime.language_command(name)
    if not command:
        raise RuntimeError('Packaged language executable unavailable: ' + name)
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=folder)
    try:
        await _write_message(process, json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
            'params':{'processId':None,'rootUri':folder.as_uri(),'capabilities':{},
                      'workspaceFolders':[{'uri':folder.as_uri(),'name':'Runtime smoke'}]}}))
        initialized = await _response(process, 1)
        if not isinstance(initialized, dict) or not initialized.get('capabilities'):
            raise RuntimeError('Packaged language server capabilities missing: ' + name)
        await _write_message(process, json.dumps({'jsonrpc':'2.0','method':'initialized','params':{}}))
        await _write_message(process, json.dumps({'jsonrpc':'2.0','id':2,'method':'shutdown','params':None}))
        await _response(process, 2)
        await _write_message(process, json.dumps({'jsonrpc':'2.0','method':'exit','params':None}))
        await asyncio.wait_for(process.wait(), 10)
        if process.returncode:
            raise RuntimeError('Packaged language server shutdown failed: ' + name)
        return {'initialized':True,'shutdown':True}
    finally:
        await _stop(process)


async def _debugpy() -> dict:
    # This qualification runs the bundled adapter itself, not the host target
    # interpreter. Launching user Python programs is validated separately.
    command = ([sys.executable, '--runtime-module', 'debugpy.adapter']
               if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'debugpy.adapter'])
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        await _write_message(process, json.dumps({'seq':1,'type':'request','command':'initialize',
            'arguments':{'clientID':'termx-runtime-smoke','adapterID':'python',
                         'linesStartAt1':True,'columnsStartAt1':True,'pathFormat':'path'}}))
        body = await _response(process, 1, dap=True)
        if not body.get('supportsConfigurationDoneRequest'):
            raise RuntimeError('Packaged Python debugger capabilities missing')
        await _write_message(process, json.dumps({'seq':2,'type':'request','command':'disconnect',
                                                  'arguments':{'terminateDebuggee':False}}))
        await _response(process, 2, dap=True)
        return {'initialized':True,'disconnect':True,'frozen_entry':bool(getattr(sys,'frozen',False))}
    finally:
        await _stop(process)


async def smoke() -> dict:
    runtime.configure_bundle()
    report = {'ui_contract':3,'frozen':bool(getattr(sys,'frozen',False)), 'executed':{}}
    node = runtime.node_binary()
    if not node:
        raise RuntimeError('Packaged Node executable unavailable')
    report['executed']['node'] = {'version':await _command([node, '--version'])}
    import imageio_ffmpeg
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    bundled = runtime.bundle_root()
    if bundled and not ffmpeg.is_relative_to(bundled.resolve()):
        raise RuntimeError('FFmpeg resolved outside the packaged runtime')
    report['executed']['ffmpeg'] = {'version':await _command([str(ffmpeg), '-version'])}
    with tempfile.TemporaryDirectory(prefix='termx-runtime-smoke-') as temporary:
        folder = Path(temporary)
        report['executed']['languages'] = {name:await _language(name, folder)
            for name in ('python','typescript','html','css','json')}
        report['executed']['debugpy'] = await _debugpy()
        from termx.development.debug import DebugService
        service = DebugService()
        try:
            session = await service.create('runtime-smoke', folder, 'javascript')
            initialized = await session.active.request('initialize', session.initialize)
            if not initialized.get('supportsConfigurationDoneRequest'):
                raise RuntimeError('Packaged JavaScript debugger capabilities missing')
            report['executed']['javascript_debug'] = {'initialized':True,'loopback_bound':True}
        finally:
            await service.close()
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto('data:text/html,<title>TermX runtime</title><main>Packaged Chromium works</main>')
                if await page.locator('main').inner_text() != 'Packaged Chromium works':
                    raise RuntimeError('Packaged Chromium rendering failed')
                screenshot = await page.screenshot()
                if not screenshot.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('Packaged Chromium screenshot failed')
                report['executed']['chromium'] = {'rendered':True,'screenshot_png':True,'version':browser.version}
            finally:
                await browser.close()
    return report
