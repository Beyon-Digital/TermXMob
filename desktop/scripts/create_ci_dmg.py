#!/usr/bin/env python3
"""Create an artifact-only signed DMG without an explicit mounted-layout phase.

The Tauri-built app must already be signed, notarized and stapled. Its copied
bytes/signatures/ticket are verified; only the new container is signed here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import sys
import tempfile
from typing import Callable

if __package__:
    from .notarize_ci_dmg import TicketError, atomic_json, digest, run_private
else:
    from notarize_ci_dmg import TicketError, atomic_json, digest, run_private


def bundle_digest(app: Path) -> str:
    value = hashlib.sha256()
    total = 0
    for count, path in enumerate(sorted(app.rglob('*')), 1):
        if count > 100000:
            raise TicketError('Application file count exceeds the bound')
        relative = path.relative_to(app).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve().is_relative_to(app.resolve()):
                raise TicketError('Application contains an external symbolic link')
            value.update(json.dumps(['link', relative, target]).encode())
        elif stat.S_ISREG(mode):
            total += path.stat().st_size
            if total > 20 * 1024**3:
                raise TicketError('Application size exceeds the bound')
            value.update(json.dumps(['file', relative, stat.S_IMODE(mode), digest(path)]).encode())
        elif not stat.S_ISDIR(mode):
            raise TicketError('Application contains a nonregular resource')
    return value.hexdigest()


def create_container(
    app_directory: Path, output_directory: Path, receipt: Path, *, config: dict,
    platform: str, source_sha: str, run_id: str, signing_identity: str,
    runner: Callable = run_private,
) -> dict:
    receipt.unlink(missing_ok=True)
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise TicketError('Invalid container source provenance')
    architecture = {'macos-arm64': ('arm64', 'aarch64'), 'macos-x86_64': ('x86_64', 'x64')}.get(platform)
    if not architecture or not signing_identity.strip() or signing_identity.strip() == '-':
        raise TicketError('A supported Mac platform and real signing identity are required')
    product, version, identifier = (config.get(name, '') for name in ('productName', 'version', 'identifier'))
    if not isinstance(product, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', product) or not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', version) or not isinstance(identifier, str) or not identifier:
        raise TicketError('Invalid source application configuration')
    apps = sorted(app_directory.glob('*.app'))
    if len(apps) != 1 or apps[0].name != product + '.app' or apps[0].is_symlink() or not apps[0].is_dir():
        raise TicketError('Expected one matching Tauri application bundle')
    app = apps[0].resolve()
    with (app / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != identifier or info.get('CFBundleShortVersionString') != version:
        raise TicketError('Application identity or version differs from source')
    executable = info.get('CFBundleExecutable', '')
    if not isinstance(executable, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', executable):
        raise TicketError('Invalid application executable')
    binary = app / 'Contents/MacOS' / executable
    if binary.is_symlink() or not binary.is_file():
        raise TicketError('A regular application executable is required')
    output_directory.mkdir(parents=True, exist_ok=True)
    if output_directory.is_symlink() or any(output_directory.glob('*.dmg')):
        raise TicketError('Refusing an ambiguous or existing container output')
    output = output_directory / f'{product}_{version}_{architecture[1]}.dmg'

    def checked(argv: list[str], phase: str, timeout: int = 120):
        try:
            result = runner(argv, timeout)
        except Exception:
            raise TicketError(f'{phase} did not complete') from None
        if result.returncode:
            raise TicketError(f'{phase} failed')
        return result

    def verify_bundle(path: Path) -> None:
        checked(['codesign', '--verify', '--deep', '--strict', str(path)], 'Application signature verification')
        checked(['xcrun', 'stapler', 'validate', str(path)], 'Application ticket validation')
        checked(['spctl', '--assess', '--type', 'execute', str(path)], 'Application Gatekeeper assessment')

    verify_bundle(app)
    arch = checked(['lipo', '-archs', str(binary)], 'Application architecture verification').stdout.strip().split()
    if arch != [architecture[0]]:
        raise TicketError('Application architecture differs from selected platform')
    source_digest = bundle_digest(app)
    binary_digest = digest(binary)
    try:
        with tempfile.TemporaryDirectory(prefix='.termx-dmg-staging-', dir=output_directory) as stage_directory:
            staging = Path(stage_directory)
            staging.chmod(0o700)
            staged = staging / app.name
            checked(['ditto', '--rsrc', '--extattr', str(app), str(staged)], 'Private application staging', timeout=300)
            verify_bundle(staged)
            if bundle_digest(staged) != source_digest or bundle_digest(app) != source_digest:
                raise TicketError('Application changed while staging the container')
            (staging / 'Applications').symlink_to('/Applications', target_is_directory=True)
            # No explicit attach, Finder, writable-layout or detach operation.
            checked(['hdiutil', 'create', '-srcfolder', str(staging), '-volname', product, '-fs', 'HFS+', '-format', 'UDZO', '-noskipunreadable', str(output)], 'Read-only DMG creation', timeout=900)
            if not output.is_file() or output.is_symlink() or output.stat().st_size == 0:
                raise TicketError('A complete regular DMG was not created')
            if bundle_digest(staged) != source_digest or bundle_digest(app) != source_digest:
                raise TicketError('Application changed during container creation')
        checked(['codesign', '--force', '--sign', signing_identity, str(output)], 'Final container signing')
        checked(['codesign', '--verify', '--strict', str(output)], 'Final container signature verification')
        checked(['hdiutil', 'verify', str(output)], 'Final container integrity verification')
        result = {
            'schema_version': 1, 'source_sha': source_sha, 'run_id': run_id, 'platform': platform,
            'bundle_identifier': identifier, 'bundle_version': version, 'executable_sha256': binary_digest,
            'unchanged_application_tree_sha256': source_digest, 'app_signature_ticket_and_gatekeeper_verified': True,
            'package': output.name, 'pre_ticket_package_sha256': digest(output), 'package_bytes': output.stat().st_size,
            'explicit_layout_mount_performed': False, 'container_signature_and_integrity_verified': True,
            'final_dmg_notarization_required': True, 'qualified': False, 'release_published': False,
        }
        atomic_json(receipt, result)
        return result
    except Exception:
        # A partial or unverified new container must never become upload input.
        output.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-directory', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.exit(1, 'Native DMG creation requires the macOS CI runner\n')
    try:
        result = create_container(args.app_directory, args.output_directory, args.receipt, config=json.loads(args.config.read_text()), platform=args.platform, source_sha=os.environ.get('GITHUB_SHA', ''), run_id=os.environ.get('GITHUB_RUN_ID', ''), signing_identity=os.environ.get('APPLE_SIGNING_IDENTITY', ''))
    except TicketError as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
