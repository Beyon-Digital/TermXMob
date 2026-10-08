#!/usr/bin/env python3
"""Notarize the signed artifact-only DMG before upload; never rebuild or publish it.

Apple's application ticket is distinct from the final disk-image ticket. A known
submission is waited on again after transient failures; an unknown submission
outcome is never replayed. Command output and credential arguments stay private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable
import uuid


class TicketError(RuntimeError):
    """A public, nonsecret CI failure."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run_private(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    # Spool rather than accumulate unlimited tool output in RAM. Never forward
    # Apple's diagnostic text: it can include credential arguments or paths.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=timeout, check=False)
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(65537), stderr.read(65537)
        if len(out) > 65536 or len(err) > 65536:
            raise TicketError('Notarization tool output exceeded the bounded limit')
        return subprocess.CompletedProcess(argv, result.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace'))


def transient(result: subprocess.CompletedProcess[str]) -> bool:
    text = (result.stdout + '\n' + result.stderr).lower()
    if any(word in text for word in ('unauthorized', 'authentication', 'forbidden', 'invalid credentials', '401', '403', 'rejected', 'invalid submission')):
        return False
    return any(word in text for word in ('network connection was lost', 'internet connection appears to be offline', 'nsurlerrordomain code=-1009', 'http 429', 'http 503', 'status code: 429', 'status code: 503', 'temporarily unavailable'))


def submission_data(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise TicketError('Notarization returned invalid JSON') from None
    if not isinstance(data, dict):
        raise TicketError('Notarization returned an invalid response')
    return data


def submission_id(data: dict) -> str:
    try:
        identifier = str(uuid.UUID(data['id']))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise TicketError('Notarization did not return a valid submission ID') from None
    return identifier


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.dmg-ticket-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def response_category(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stdout + '\n' + result.stderr).lower()
    if any(word in text for word in ('unauthorized', 'authentication', 'forbidden', 'invalid credentials', '401', '403')):
        return 'authentication-failure'
    if any(word in text for word in ('rejected', 'invalid submission')):
        return 'rejected'
    try:
        status = submission_data(result).get('status')
    except TicketError:
        status = None
    if status in ('Invalid', 'Rejected'):
        return 'rejected'
    if transient(result):
        return 'transient-transport'
    if 'timeout' in text or 'timed out' in text:
        return 'wait-timeout-signal'
    return 'success' if result.returncode == 0 else 'unknown-tool-failure'


def notarize(
    directory: Path, receipt: Path, *, source_sha: str, run_id: str,
    platform: str, credentials: dict[str, str], runner: Callable = run_private,
    clock: Callable = time.monotonic, sleep: Callable = time.sleep,
    budget_seconds: float = 900,
) -> dict:
    # Do not leave an earlier success receipt usable after a failed new attempt.
    receipt.unlink(missing_ok=True)
    diagnostic_path = receipt.parent / 'submission.json'
    diagnostic_path.unlink(missing_ok=True)
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise TicketError('Invalid source provenance')
    if platform not in ('macos-arm64', 'macos-x86_64') or not 1 <= budget_seconds <= 1200:
        raise TicketError('Invalid notarization platform or time budget')
    packages = sorted(directory.glob('*.dmg'))
    if len(packages) != 1 or packages[0].is_symlink() or not packages[0].is_file():
        raise TicketError('Expected exactly one regular final DMG')
    package = packages[0].resolve()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.dmg', package.name):
        raise TicketError('Invalid final DMG filename')
    key, key_id, issuer = (credentials.get(name, '') for name in ('APPLE_API_KEY_PATH', 'APPLE_API_KEY', 'APPLE_API_ISSUER'))
    key_path = Path(key)
    if not key or key_path.is_symlink() or not key_path.is_file() or stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        raise TicketError('A private API key file is required')
    if not re.fullmatch(r'[A-Za-z0-9]{5,40}', key_id):
        raise TicketError('An API key identifier is required')
    try:
        uuid.UUID(issuer)
    except (ValueError, TypeError, AttributeError):
        raise TicketError('An API key issuer is required') from None
    deadline = clock() + budget_seconds
    before = digest(package)
    size = package.stat().st_size

    def unchanged() -> None:
        if package.is_symlink() or package.stat().st_size != size or digest(package) != before:
            raise TicketError('Final DMG changed before ticket stapling')

    diagnostic = {
        'schema_version': 1, 'source_sha': source_sha, 'run_id': run_id,
        'platform': platform, 'package': package.name, 'pre_ticket_sha256': before,
        'submission_id': None, 'qualified': False, 'phases': [],
    }

    def save_diagnostic() -> None:
        atomic_json(diagnostic_path, diagnostic)

    def invoke(argv: list[str], stage: str, *, limit: float = 120, reconcile_timeout: bool = False) -> subprocess.CompletedProcess[str]:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TicketError('DMG notarization exceeded its time budget')
        started = clock()
        try:
            result = runner(argv, min(remaining, limit))
        except subprocess.TimeoutExpired:
            diagnostic['phases'].append({'stage': stage, 'returncode': None, 'category': 'process-timeout', 'elapsed_seconds': round(max(0, clock() - started), 3)})
            save_diagnostic()
            if reconcile_timeout:
                return subprocess.CompletedProcess(argv, 124, '', '')
            raise TicketError(f'{stage} did not complete; submission is not replayed') from None
        except (TicketError, OSError):
            diagnostic['phases'].append({'stage': stage, 'returncode': None, 'category': 'tool-failure', 'elapsed_seconds': round(max(0, clock() - started), 3)})
            save_diagnostic()
            raise TicketError(f'{stage} did not complete; submission is not replayed') from None
        diagnostic['phases'].append({'stage': stage, 'returncode': result.returncode, 'category': response_category(result), 'elapsed_seconds': round(max(0, clock() - started), 3)})
        save_diagnostic()
        return result

    def checked(argv: list[str], stage: str) -> None:
        if invoke(argv, stage).returncode:
            raise TicketError(f'{stage} failed')

    checked(['codesign', '--verify', '--strict', str(package)], 'DMG signature verification')
    unchanged()
    auth = ['--key', str(key_path), '--key-id', key_id, '--issuer', issuer, '--output-format', 'json']
    result = invoke(['xcrun', 'notarytool', 'submit', str(package), *auth], 'DMG submission', limit=180)
    # An unsuccessful submit may still have reached Apple. Never upload it
    # again without a returned ID; only retry polling that exact known ID.
    if result.returncode:
        try:
            identifier = submission_id(submission_data(result))
        except TicketError:
            raise TicketError('DMG submission outcome is unknown; submission is not replayed') from None
        if not transient(result):
            raise TicketError('DMG submission failed with a known submission; inspect it before retrying')
        accepted = False
    else:
        data = submission_data(result)
        identifier = submission_id(data)
        status = data.get('status')
        if status in ('Invalid', 'Rejected'):
            raise TicketError('DMG notarization was rejected')
        # Apple's non-wait submit envelope may contain only id/message/path.
        # A valid upload ID is pending until wait returns exact-ID Accepted.
        if status not in (None, 'Uploaded', 'In Progress', 'Accepted'):
            raise TicketError('DMG submission returned an unknown status')
        accepted = status == 'Accepted'
    diagnostic['submission_id'] = identifier
    save_diagnostic()
    # Publish only safe identity/hash metadata so a known submission is not lost
    # when CI fails. This is pending evidence, never a package qualification.
    print(json.dumps({'stage': 'known-submission', 'submission_id': identifier, 'pre_ticket_sha256': before}), flush=True)
    if not accepted:
        for attempt in range(3):
            unchanged()
            remaining = deadline - clock()
            wait_seconds = max(1, min(240, int(remaining) - 5))
            result = invoke(['xcrun', 'notarytool', 'wait', identifier, *auth, '--timeout', f'{wait_seconds}s'], 'DMG acceptance wait', limit=wait_seconds + 5, reconcile_timeout=True)
            if result.returncode == 0:
                data = submission_data(result)
                if submission_id(data) != identifier:
                    raise TicketError('DMG acceptance referred to a different submission')
                if data.get('status') != 'Accepted':
                    raise TicketError('DMG notarization did not return Accepted')
                accepted = True
                break
            if response_category(result) in ('authentication-failure', 'rejected'):
                raise TicketError('DMG acceptance wait failed')
            unchanged()
            # Reconcile structured server state for this known ID instead of
            # guessing Apple's localized/unstructured wait-timeout wording.
            info = invoke(['xcrun', 'notarytool', 'info', identifier, *auth], 'DMG submission reconciliation', limit=60)
            if info.returncode:
                raise TicketError('DMG submission reconciliation failed')
            data = submission_data(info)
            if submission_id(data) != identifier:
                raise TicketError('DMG reconciliation referred to a different submission')
            status = data.get('status')
            if status == 'Accepted':
                accepted = True
                break
            if status != 'In Progress':
                raise TicketError('DMG submission reconciliation did not return pending or Accepted')
            if attempt == 2:
                raise TicketError('DMG acceptance wait failed')
            sleep(min(5 * (attempt + 1), max(0, deadline - clock())))
    if not accepted:
        raise TicketError('DMG notarization was not accepted')
    unchanged()
    checked(['xcrun', 'stapler', 'staple', str(package)], 'DMG ticket stapling')
    checked(['xcrun', 'stapler', 'validate', str(package)], 'DMG ticket validation')
    checked(['hdiutil', 'verify', str(package)], 'DMG integrity verification')
    checked(['codesign', '--verify', '--strict', str(package)], 'Final DMG signature verification')
    result = {
        'schema_version': 1, 'source_sha': source_sha, 'run_id': run_id,
        'platform': platform, 'package': package.name, 'submission_id': identifier,
        'notarization_status': 'Accepted', 'pre_ticket_sha256': before,
        'final_package_sha256': digest(package), 'final_package_bytes': package.stat().st_size,
        'signature_verified': True, 'ticket_stapled_and_validated': True,
        'disk_image_verified': True, 'release_published': False,
    }
    atomic_json(receipt, result)
    diagnostic['qualified'] = True
    diagnostic['final_package_sha256'] = result['final_package_sha256']
    save_diagnostic()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--platform', required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.exit(1, 'DMG notarization requires the macOS CI runner\n')
    try:
        result = notarize(args.directory, args.receipt, source_sha=os.environ.get('GITHUB_SHA', ''), run_id=os.environ.get('GITHUB_RUN_ID', ''), platform=args.platform, credentials=os.environ)
    except TicketError as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
