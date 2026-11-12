#!/usr/bin/env python3
"""Source-backend fixture for manual macOS Tauri media-studio verification.

This is not an installed-app or provider-generation qualification. It prepares
an isolated debug app and immutable UI snapshot, then serves the real host with
an owned image receipt only when a synthetic fixture owner already exists.
The optional instrument command applies a saved test-only own-WKWebView probe.
The production bridge and its OS credential storage remain in use; this harness
does not sign in or create an owner, password, session or refresh credential.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from time import sleep, time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args):
    repo, root, binary = args.repo.resolve(), args.root.resolve(), args.binary.resolve()
    if root.exists():
        raise RuntimeError('Choose a fresh fixture root; existing state is never overwritten')
    root.mkdir(mode=0o700, parents=True)
    native = root / 'native'
    native.mkdir(mode=0o700)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config = native / 'desktop.json'
    config.write_text(json.dumps({'passcode': 'studio-native-fixture-bootstrap', 'port': port,
                                  'onboarded': True, 'backend_mode': 'app'}) + '\n')
    config.chmod(0o600)
    ui = root / 'fixture-ui-build'
    shutil.copytree(repo / 'desktop/workspace/dist', ui)
    manifest = {p.relative_to(ui).as_posix(): digest(p) for p in sorted(ui.rglob('*')) if p.is_file()}
    (root / 'ui-snapshot.json').write_text(json.dumps(manifest, indent=2) + '\n')
    script = root / 'studio_native_smoke.py'
    shutil.copy2(__file__, script)
    app = root / 'Termx Studio Fixture.app'
    macos, resources = app / 'Contents/MacOS', app / 'Contents/Resources'
    macos.mkdir(parents=True)
    (resources / 'backend').mkdir(parents=True)
    executable = macos / 'termx-studio-fixture'
    shutil.copy2(binary, executable)
    executable.chmod(0o755)
    # A unique bundle identity keeps WKWebView's default store independent.
    bundle_id = 'com.jaexxxy.termx.studiofixture.' + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    plist = {'CFBundleName': 'Termx Studio Fixture', 'CFBundleDisplayName': 'Termx Studio Fixture',
             'CFBundleIdentifier': bundle_id, 'CFBundleExecutable': 'termx-studio-fixture',
             'CFBundlePackageType': 'APPL', 'CFBundleVersion': '0.2.11',
             'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0'}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(plist))
    environment = {'TERMX_DESKTOP_DATA_DIR': str(native), 'TERMX_STUDIO_NATIVE_FIXTURE_ROOT': str(root),
                   'TERMX_ENGINE_STARTUP_REFRESH': '0', 'TERMX_AGENTS_DIR': str(root / 'agents')}
    if args.probe_output:
        args.probe_output.resolve().mkdir(mode=0o700, parents=True, exist_ok=False)
        environment['TERMX_FIXTURE_PROBE_DIR'] = str(args.probe_output.resolve())
        if args.probe_updater:
            environment['TERMX_FIXTURE_PROBE_UPDATER'] = '1'
    elif args.probe_updater:
        raise RuntimeError('--probe-updater requires --probe-output')
    launcher = macos / 'launch-fixture'
    launcher.write_text('#!/bin/sh\n' + ''.join('export ' + k + '=' + shlex.quote(v) + '\n'
                                              for k, v in environment.items()) +
                        'exec ' + shlex.quote(str(executable)) + '\n')
    launcher.chmod(0o755)
    backend = resources / 'backend/termx-backend'
    backend.write_text('#!/bin/sh\nexec ' + shlex.quote(str(repo / '.venv/bin/python')) + ' ' +
                       shlex.quote(str(script)) + ' serve --repo ' + shlex.quote(str(repo)) + ' "$@"\n')
    backend.chmod(0o755)
    source = repo / 'desktop/src-tauri'
    rust_sources = {p.relative_to(source).as_posix(): digest(p) for p in sorted(source.rglob('*'))
                    if p.is_file() and p.suffix in {'.rs', '.m', '.h', '.c', '.toml', '.json'}}
    report = {'proof': 'isolated-source-tauri-native', 'app': str(app), 'bundle_id': bundle_id,
              'fixture_root': str(root), 'port': port, 'binary_sha256': digest(binary),
              'rust_source_snapshot': rust_sources, 'ui_snapshot_files': len(manifest),
              'provider_effects': 0,
              'limitations': ['Local debug shell, not a signed installer or release package.',
                              'Source host with blocked provider transport; an owned image is seeded only for a pre-existing synthetic owner.',
                              'Harness creates no owner, password, session or refresh credential; native sign-in is not automated.']}
    (root / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'rust_source_snapshot'}, indent=2))


def serve(args):
    root = Path(os.environ['TERMX_STUDIO_NATIVE_FIXTURE_ROOT']).resolve()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo / 'src'))
    os.environ['TERMX_CONFIG_DIR'] = str(root / 'native/config')
    os.environ['TERMX_DESKTOP'] = '1'
    os.environ['TERMX_ENGINE_STARTUP_REFRESH'] = '0'
    os.environ['TERMX_AGENTS_DIR'] = str(root / 'agents')
    import httpx
    from PIL import Image, ImageDraw
    import uvicorn
    from termx.app import AppState, create_app
    from termx.agent.secrets import CredentialStore
    from termx.identity import Identity
    from termx.media.studio import SNAPSHOT
    from termx.notify import ready

    state = AppState(passcode=args.passcode, port=args.port, credentials=CredentialStore({}))
    app = create_app(state, web_dir=root / 'fixture-ui-build')
    # Never create an account, password, native session or refresh credential.
    owner = output = job_id = None
    if state.identity.configured:
        owner = state.identity.principal_for(Identity('termx:local', 'native-studio-fixture', 'password'))
        if owner is None:
            raise RuntimeError('The existing fixture does not contain its synthetic owner')
        image = Image.new('RGB', (800, 480), '#245448')
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 760, 440), outline='#9ee6bc', width=6)
        draw.text((90, 180), 'NATIVE WKWEBVIEW', fill='#ffffff', font_size=52)
        draw.text((90, 260), 'Owned artifact - fixture output', fill='#cdf4dd', font_size=30)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        artifact = state.artifacts.create(owner.id, None, 'image', 'Native fixture output',
                                          blob=buffer.getvalue(), mime='image/png')
        output = f'/api/media/artifacts/{artifact["id"]}/export?format=original'
        job_id = 'native-studio-owned-image-fixture'
        now = time()
        with state.studio.db() as db:
            db.execute('''INSERT OR REPLACE INTO studio_jobs(id,owner,project,request_id,digest,provider_id,
                       adapter,model,kind,prompt,status,created,updated,outputs)
                       VALUES(?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)''',
                       (job_id, owner.id, 'native-studio-owned-image-fixture', 'fixture-seeded',
                        'native-fixture-no-provider', 'openrouter', 'fixture/owned-image', 'image',
                        'Synthetic owned image for native authenticated preview verification', 'completed',
                        now, now, json.dumps([output])))
    elif os.environ.get('TERMX_STUDIO_REQUIRE_EXISTING_OWNER') == '1':
        raise RuntimeError('An existing synthetic owner is required; credential creation is disabled')
    def blocked(request):
        if request.method == 'GET' and request.url.path in ('/api/v1/images/models', '/api/v1/videos/models'):
            return httpx.Response(503, json={'error': 'Explicit offline native fixture'})
        raise AssertionError('Provider transport is blocked in the native fixture')
    client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    state.studio.client = client
    state.studio.openrouter.client = client
    state.studio._catalog = SNAPSHOT['openrouter']
    state.studio._catalog_at = time()
    audit = root / 'backend-http.jsonl'
    @app.middleware('http')
    async def record(request, call_next):
        response = await call_next(request)
        row = {'method': request.method, 'path': request.url.path,
               'status': response.status_code, 'bearer': request.headers.get('authorization', '').startswith('Bearer '),
               'cookie': bool(request.headers.get('cookie')), 'user_agent': request.headers.get('user-agent', '')}
        with audit.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        return response
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, log_level='error'))
    state.request_shutdown = lambda: setattr(server, 'should_exit', True)
    if args.parent_pid:
        # The normal source sidecar also exits when its exact native parent ends.
        from termx.cli import watch_parent
        watch_parent(args.parent_pid, state.request_shutdown)
    metadata = {'host_id': state.identity.host_id, 'owner_id': owner.id if owner else None,
                'image_path': output, 'job_id': job_id, 'port': args.port}
    (root / 'backend-fixture.json').write_text(json.dumps(metadata, indent=2) + '\n')
    def announce():
        while not server.started:
            sleep(.05)
        ready(args.port, [f'http://127.0.0.1:{args.port}/'])
    threading.Thread(target=announce, daemon=True).start()
    server.run()


def instrument(args):
    """Copy production source and apply only the reviewable test probe patch."""
    repo, root = args.repo.resolve(), args.root.resolve()
    if root.exists():
        raise RuntimeError('Choose a fresh instrumentation root')
    patch = repo / 'tests/fixtures/media_studio_native_probe.patch'
    if not patch.is_file():
        raise RuntimeError('The saved native probe instrumentation patch is required')
    root.mkdir(mode=0o700, parents=True)
    shutil.copytree(repo / 'desktop/src-tauri', root / 'src-tauri')
    shutil.copytree(repo / 'desktop/bootstrap', root / 'bootstrap')
    shutil.copy2(patch, root / 'native-probe-instrumentation.patch')
    subprocess.run(['patch', '-p1', '-i', str(patch)], cwd=root / 'src-tauri', check=True)
    print(json.dumps({'instrumented_source': str(root / 'src-tauri'),
                      'patch_sha256': digest(patch), 'production_source_modified': False,
                      'build_note': 'Use a separate --target-dir for the instrumented source copy.'}, indent=2))


def verify(args):
    """Check actual native probe output, including the updater's separate ACL."""
    output = args.output.resolve()
    main = json.loads((output / 'dom.json').read_text())
    assert main['ready'] == 'complete' and main['tauri'] and main['signIn'], main
    assert not main['workspace'], 'This credential-free proof must stay signed out'
    assert not main['pageErrors'], main['pageErrors']
    assert not [text for text in main['alerts'] if text.strip()], main['alerts']
    assert main['methods']['status'] == 200 and not main['methodsError'], main
    assert main['mediaGate'] == {'rejected': True, 'error': 'Sign in required'}, main['mediaGate']
    assert {item['type'] for item in main['inputs']} >= {'text', 'password'}, main['inputs']
    screenshots = [output / 'native-signin.png']
    if args.updater:
        updater = json.loads((output / 'updater/dom.json').read_text())
        assert updater['ready'] == 'complete' and updater['tauri'], updater
        assert updater['href'].endswith('/updater.html'), updater['href']
        assert not updater['pageErrors'], updater['pageErrors']
        assert 'workspace_request not allowed' in updater['methodsError'], updater['methodsError']
        assert updater['mediaGate']['rejected'] and 'workspace_binary not allowed' in updater['mediaGate']['error'], updater['mediaGate']
        screenshots.append(output / 'updater/native-signin.png')
    for screenshot in screenshots:
        raw = screenshot.read_bytes()
        assert raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) > 10000, screenshot
    if args.identity_db:
        import sqlite3
        with sqlite3.connect(args.identity_db.resolve()) as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0, 'Native session unexpectedly created'
    result = {'result': 'passed', 'scope': 'instrumented own logged-out WKWebView; authenticated studio remains unverified',
              'native_page_errors': [], 'auth_methods_ipc_status': 200,
              'protected_media_denied': True, 'updater_workspace_acl_preserved': bool(args.updater),
              'screenshots': [str(path) for path in screenshots]}
    (output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--repo', type=Path, required=True)
    prep.add_argument('--root', type=Path, required=True)
    prep.add_argument('--binary', type=Path, required=True)
    prep.add_argument('--probe-output', type=Path)
    prep.add_argument('--probe-updater', action='store_true')
    probe = sub.add_parser('instrument')
    probe.add_argument('--repo', type=Path, required=True)
    probe.add_argument('--root', type=Path, required=True)
    check = sub.add_parser('verify')
    check.add_argument('--output', type=Path, required=True)
    check.add_argument('--updater', action='store_true')
    check.add_argument('--identity-db', type=Path)
    backend = sub.add_parser('serve')
    backend.add_argument('--repo', type=Path, required=True)
    backend.add_argument('--port', type=int, required=True)
    backend.add_argument('--passcode', required=True)
    backend.add_argument('--desktop', action='store_true')
    backend.add_argument('--host')
    backend.add_argument('--parent-pid', type=int)
    backend.add_argument('--broker')
    args = parser.parse_args()
    {'prepare': prepare, 'serve': serve, 'instrument': instrument, 'verify': verify}[args.operation](args)


if __name__ == '__main__':
    main()
