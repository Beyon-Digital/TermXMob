#!/usr/bin/env python3
"""Install an authenticated, matching CI DMG without rebuilding its binary."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

from native_gui_smoke import file_digest


def validate_source(run, expected_sha):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('An exact forty-character source commit is required')
    if run.get('head_sha') != expected_sha:
        raise ValueError('Artifact source run does not match the selected exact commit')
    if run.get('event') != 'workflow_dispatch' or run.get('path') != '.github/workflows/desktop.yml':
        raise ValueError('Expected the desktop artifact workflow dispatch')


def select_artifact(rows, platform, number):
    expected = f'termx-{platform}-{number}'
    matching = [row for row in rows if row.get('name') == expected and not row.get('expired')]
    if len(matching) != 1:
        raise ValueError('Expected exactly one unexpired matching platform artifact')
    return matching[0]


def validate_bundle(info, source_config):
    if info.get('CFBundleIdentifier') != source_config.get('identifier'):
        raise ValueError('Signed application bundle identity differs from its source commit')
    if info.get('CFBundleShortVersionString') != source_config.get('version'):
        raise ValueError('Signed application version differs from its source commit')


def command(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Artifact verification command failed: '+Path(args[0]).name+' '+result.stderr[-1400:])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--source-run', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--platform', choices=['macos-arm64', 'macos-x86_64'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--environment-file', type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('DMG installation and signature checks run on macOS CI')
    if not re.fullmatch(r'[0-9]+', args.source_run) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        parser.error('Invalid repository or source-run identity')
    run = json.loads(command(['gh', 'api', f'repos/{args.repo}/actions/runs/{args.source_run}']).stdout)
    validate_source(run, args.source_sha)
    source_file = json.loads(command(['gh', 'api', f'repos/{args.repo}/contents/desktop/src-tauri/tauri.conf.json?ref={args.source_sha}']).stdout)
    source_config = json.loads(base64.b64decode(source_file['content']))
    jobs = json.loads(command(['gh', 'api', f'repos/{args.repo}/actions/runs/{args.source_run}/jobs?per_page=100']).stdout)['jobs']
    qualified = [job for job in jobs if job['name'] == args.platform and job['conclusion'] == 'success']
    if len(qualified) != 1:
        raise RuntimeError('Selected platform installer job has not completed successfully')
    artifacts = json.loads(command(['gh', 'api', f'repos/{args.repo}/actions/runs/{args.source_run}/artifacts?per_page=100']).stdout)['artifacts']
    artifact = select_artifact(artifacts, args.platform, run['run_number'])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_dir, mount, installation = output/'artifact', output/'mount', output/'installed'
    for directory in [archive_dir, mount, installation]:
        directory.mkdir()
    command(['gh', 'run', 'download', args.source_run, '--repo', args.repo, '--name', artifact['name'], '--dir', str(archive_dir)], 600)
    packages = list(archive_dir.rglob('*.dmg'))
    if len(packages) != 1:
        raise RuntimeError('Matching installer artifact must contain exactly one DMG')
    package = packages[0]
    command(['/usr/bin/hdiutil', 'verify', str(package)], 180)
    command(['/usr/bin/xcrun', 'stapler', 'validate', str(package)], 90)
    mounted = False
    try:
        command(['/usr/bin/hdiutil', 'attach', str(package), '-readonly', '-nobrowse', '-mountpoint', str(mount)], 90)
        mounted = True
        apps = list(mount.glob('*.app'))
        if len(apps) != 1:
            raise RuntimeError('Verified DMG must contain exactly one application')
        source_app = apps[0]
        with (source_app/'Contents/Info.plist').open('rb') as info_file:
            bundle_info = plistlib.load(info_file)
        validate_bundle(bundle_info, source_config)
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(source_app)], 90)
        gatekeeper = command(['/usr/sbin/spctl', '--assess', '--type', 'execute', '--verbose=2', str(source_app)], 90)
        destination = installation/source_app.name
        command(['/usr/bin/ditto', '--rsrc', '--extattr', str(source_app), str(destination)], 180)
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)], 90)
        binary = destination/'Contents/MacOS/termx-desktop'
        if file_digest(binary) != file_digest(source_app/'Contents/MacOS/termx-desktop'):
            raise RuntimeError('Installed native executable differs from the signed DMG')
        architectures = command(['/usr/bin/lipo', '-archs', str(binary)]).stdout.split()
        expected_arch = 'arm64' if args.platform == 'macos-arm64' else 'x86_64'
        if expected_arch not in architectures or os.uname().machine != expected_arch:
            raise RuntimeError('Installer architecture does not match the selected native runner')
        report = {
            'source_run': int(args.source_run), 'source_sha': args.source_sha, 'source_workflow': run['path'],
            'source_run_url': run['html_url'], 'source_platform_job': qualified[0]['html_url'],
            'platform': args.platform, 'artifact_id': artifact['id'], 'artifact_name': artifact['name'],
            'artifact_digest_metadata': artifact.get('digest'), 'package_sha256': file_digest(package),
            'binary_sha256': file_digest(binary), 'app': str(destination), 'package': str(package),
            'dmg_verified': True, 'notarization_staple_validated': True,
            'deep_strict_signature_verified_before_and_after_copy': True,
            'gatekeeper_assessment': (gatekeeper.stdout+gatekeeper.stderr).strip(),
            'architecture': architectures, 'rebuilt': False, 'quarantine_removed': False,
            'bundle_identifier': bundle_info['CFBundleIdentifier'],
            'bundle_version': bundle_info['CFBundleShortVersionString'],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n')
        if args.environment_file:
            if any('\n' in str(path) or '\r' in str(path) for path in [destination, package]):
                raise RuntimeError('Invalid installer fixture path')
            with args.environment_file.open('a') as environment:
                environment.write(f'TERMX_MAC_GUI_APP={destination}\nTERMX_MAC_GUI_DMG={package}\n')
    finally:
        if mounted:
            # A failed detach is visible; do not conceal DiskArbitration errors.
            command(['/usr/bin/hdiutil', 'detach', str(mount)], 60)


if __name__ == '__main__':
    main()
