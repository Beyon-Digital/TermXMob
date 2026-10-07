"""Synthetic command-contract evidence; no Apple account or local installer build."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

_spec = importlib.util.spec_from_file_location('notarize_ci_dmg', Path(__file__).parents[1] / 'desktop/scripts/notarize_ci_dmg.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
SUBMISSION = '32ff1362-7bfb-4e61-9144-f0e96e7cb951'
SHA = '7f1160f628c74499fbcf04be1ba7cb65b2788d55'
SECRET = 'synthetic-private-key-must-never-appear'


@pytest.fixture
def fixture(tmp_path):
    directory = tmp_path / 'bundle'
    directory.mkdir()
    package = directory / 'Termx_0.2.6_aarch64.dmg'
    package.write_bytes(b'synthetic-signed-final-container')
    key = tmp_path / 'AuthKey.p8'
    key.write_text(SECRET)
    key.chmod(0o600)
    return {'directory': directory, 'package': package, 'receipt': tmp_path / 'evidence/receipt.json', 'credentials': {'APPLE_API_KEY_PATH': str(key), 'APPLE_API_KEY': 'SYNTHETIC1', 'APPLE_API_ISSUER': '1c198985-8948-4444-9d22-90f7e1df6491'}}


class Commands:
    def __init__(self, fixture, *, submit=None, wait=None, fail=None, mutate=None):
        self.fixture, self.submit, self.wait, self.fail, self.mutate = fixture, list(submit or []), list(wait or []), fail, mutate
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        assert 0 < timeout <= 245
        stage = ' '.join(argv[:3])
        if self.mutate == stage:
            self.fixture['package'].write_bytes(b'modified-without-consent')
        if self.fail == stage:
            return subprocess.CompletedProcess(argv, 1, SECRET, SECRET)
        if argv[:3] == ['xcrun', 'notarytool', 'submit']:
            if self.submit:
                result = self.submit.pop(0)
                if isinstance(result, Exception):
                    raise result
                return subprocess.CompletedProcess(argv, *result)
            return subprocess.CompletedProcess(argv, 0, json.dumps({'id': SUBMISSION, 'status': 'Uploaded'}), '')
        if argv[:3] == ['xcrun', 'notarytool', 'wait']:
            if self.wait:
                return subprocess.CompletedProcess(argv, *self.wait.pop(0))
            return subprocess.CompletedProcess(argv, 0, json.dumps({'id': SUBMISSION, 'status': 'Accepted'}), '')
        if argv[:3] == ['xcrun', 'stapler', 'staple']:
            self.fixture['package'].write_bytes(self.fixture['package'].read_bytes() + b'-ticket')
        return subprocess.CompletedProcess(argv, 0, '', '')


def execute(fixture, runner, **kwargs):
    return module.notarize(fixture['directory'], fixture['receipt'], source_sha=SHA, run_id='37687927763', platform='macos-arm64', credentials=fixture['credentials'], runner=runner, sleep=lambda _: None, **kwargs)


def test_exact_final_container_gets_distinct_accepted_ticket_before_receipt(fixture):
    runner = Commands(fixture)
    result = execute(fixture, runner)
    commands = [argv for argv, _ in runner.calls]
    assert [argv[:3] for argv in commands] == [
        ['codesign', '--verify', '--strict'], ['xcrun', 'notarytool', 'submit'],
        ['xcrun', 'notarytool', 'wait'], ['xcrun', 'stapler', 'staple'],
        ['xcrun', 'stapler', 'validate'], ['hdiutil', 'verify', str(fixture['package'])],
        ['codesign', '--verify', '--strict'],
    ]
    assert commands[1][3] == str(fixture['package'])
    assert commands[2][3] == SUBMISSION
    assert '--wait' not in commands[1] and '--output-format' in commands[1]
    assert result['pre_ticket_sha256'] != result['final_package_sha256'] == module.digest(fixture['package'])
    assert result['submission_id'] == SUBMISSION and result['notarization_status'] == 'Accepted'
    assert result == json.loads(fixture['receipt'].read_text())
    assert SECRET not in fixture['receipt'].read_text()
    assert str(fixture['credentials']['APPLE_API_KEY_PATH']) not in fixture['receipt'].read_text()
    assert result['release_published'] is False


def test_known_submission_is_waited_on_not_resubmitted_after_service_outage(fixture):
    runner = Commands(fixture, wait=[(1, '', 'HTTP 503 temporarily unavailable'), (1, '', 'The wait timeout was exceeded')])
    execute(fixture, runner)
    submits = [argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'submit']]
    waits = [argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'wait']]
    assert len(submits) == 1 and len(waits) == 3
    assert all(argv[3] == SUBMISSION for argv in waits)


def test_transient_failed_submit_with_known_id_never_reuploads(fixture):
    runner = Commands(fixture, submit=[(1, json.dumps({'id': SUBMISSION}), 'HTTP 503')])
    execute(fixture, runner)
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'submit']]) == 1


@pytest.mark.parametrize('response', [
    (0, json.dumps({'id': SUBMISSION, 'status': 'Invalid'}), ''),
    (0, 'not-json-' + SECRET, ''),
    (0, json.dumps({'status': 'Uploaded'}), ''),
    (1, SECRET, 'unauthorized 401 ' + SECRET),
    (1, SECRET, 'unknown ' + SECRET),
])
def test_submission_failures_never_staple_or_leave_success_receipt(fixture, response):
    fixture['receipt'].parent.mkdir()
    fixture['receipt'].write_text('old-success')
    runner = Commands(fixture, submit=[response])
    with pytest.raises(module.TicketError) as error:
        execute(fixture, runner)
    assert SECRET not in str(error.value)
    assert not fixture['receipt'].exists()
    assert not any(argv[:2] == ['xcrun', 'stapler'] for argv, _ in runner.calls)


def test_unknown_submit_timeout_is_not_silently_replayed(fixture):
    runner = Commands(fixture, submit=[subprocess.TimeoutExpired(['secret-command', SECRET], 180)])
    with pytest.raises(module.TicketError, match='not replayed') as error:
        execute(fixture, runner)
    assert SECRET not in str(error.value) and len(runner.calls) == 2
    assert not fixture['receipt'].exists()


@pytest.mark.parametrize('data', [{'status': 'Invalid'}, {'status': 'In Progress'}, {'status': 'Accepted', 'id': '192a73f6-3f6d-47d6-8c65-84f11d776fa3'}, {'status': 'Accepted'}, {}])
def test_only_exact_accepted_submission_can_be_stapled(fixture, data):
    runner = Commands(fixture, wait=[(0, json.dumps(data), '')])
    with pytest.raises(module.TicketError):
        execute(fixture, runner)
    assert not any(argv[:2] == ['xcrun', 'stapler'] for argv, _ in runner.calls)


@pytest.mark.parametrize('fail', ['codesign --verify --strict', 'xcrun stapler staple', 'xcrun stapler validate', 'hdiutil verify '])
def test_signature_ticket_or_integrity_failure_never_publishes_receipt(fixture, fail):
    if fail.endswith(' '):
        fail += str(fixture['package'])
    with pytest.raises(module.TicketError) as error:
        execute(fixture, Commands(fixture, fail=fail))
    assert SECRET not in str(error.value) and not fixture['receipt'].exists()


def test_package_mutation_after_submission_prevents_stapling(fixture):
    runner = Commands(fixture, mutate='xcrun notarytool submit')
    with pytest.raises(module.TicketError, match='changed'):
        execute(fixture, runner)
    assert not any(argv[:2] == ['xcrun', 'stapler'] for argv, _ in runner.calls)


def test_transient_retry_is_bounded_and_known_wait_never_reuploads(fixture):
    runner = Commands(fixture, wait=[(1, '', 'HTTP 503')] * 3)
    with pytest.raises(module.TicketError, match='wait failed'):
        execute(fixture, runner)
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'wait']]) == 3
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'submit']]) == 1


@pytest.mark.parametrize('kind', ['multiple', 'symlink', 'public-key'])
def test_ambiguous_or_unsafe_package_and_key_fail_before_apple_call(fixture, kind):
    if kind == 'multiple':
        (fixture['directory'] / 'other.dmg').write_bytes(b'other')
    elif kind == 'symlink':
        original = fixture['package'].read_bytes()
        fixture['package'].unlink()
        other = fixture['directory'].parent / 'outside'
        other.write_bytes(original)
        fixture['package'].symlink_to(other)
    else:
        Path(fixture['credentials']['APPLE_API_KEY_PATH']).chmod(0o644)
    runner = Commands(fixture)
    with pytest.raises(module.TicketError):
        execute(fixture, runner)
    assert not runner.calls


def test_workflow_blocks_artifact_upload_on_missing_final_dmg_ticket():
    data = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/desktop.yml').read_text())
    jobs = data['jobs']
    steps = next(job['steps'] for job in jobs.values() if any(step.get('name') == 'Build installers' for step in job.get('steps', [])))
    names = [step.get('name', '') for step in steps]
    ticket = steps[names.index('Notarize and verify final artifact-only macOS DMG')]
    assert ticket['if'] == "runner.os == 'macOS' && steps.release.outputs.tag == ''"
    assert ticket['id'] == 'dmg_ticket'
    assert 'continue-on-error' not in ticket and 'notarize_ci_dmg.py' in ticket['run']
    assert names.index('Build installers') < names.index(ticket['name'])
    upload = next(step for step in steps if step.get('name') == 'Upload final macOS DMG ticket receipt')
    assert upload['with']['if-no-files-found'] == 'error'
    installers = next(step for step in steps if step.get('name') == 'Upload installers as workflow artifacts')
    assert installers['if'] == "always() && (runner.os != 'macOS' || steps.release.outputs.tag != '' || steps.dmg_ticket.outcome == 'success')"
    assert names.index(ticket['name']) < names.index(installers['name'])
    assert 'stapler' in (Path(__file__).parents[1] / 'desktop/scripts/fetch_macos_installer.py').read_text()


def test_unidentified_network_failure_never_replays_unknown_upload(fixture):
    runner = Commands(fixture, submit=[(1, '', 'internet connection appears to be offline')])
    with pytest.raises(module.TicketError, match='not replayed'):
        execute(fixture, runner)
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'submit']]) == 1


def test_auth_failure_with_timeout_text_does_not_poll_again(fixture):
    runner = Commands(fixture, wait=[(1, '', '401 unauthorized timeout ' + SECRET)])
    with pytest.raises(module.TicketError, match='wait failed') as error:
        execute(fixture, runner)
    assert SECRET not in str(error.value)
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'wait']]) == 1


def test_deadline_exhaustion_before_effect_fails_closed(fixture):
    times = iter([0, 901])
    runner = Commands(fixture)
    with pytest.raises(module.TicketError, match='time budget'):
        execute(fixture, runner, clock=lambda: next(times))
    assert not runner.calls and not fixture['receipt'].exists()


@pytest.mark.parametrize('diagnostic', ['rejected: the wait timeout was exceeded', 'invalid submission: the wait timeout was exceeded', 'arbitrary timeout in private diagnostic', 'HTTP 503 rejected: the wait timeout was exceeded'])
def test_rejected_or_unrecognized_timeout_is_not_a_retry_signal(fixture, diagnostic):
    runner = Commands(fixture, wait=[(1, '', diagnostic)])
    with pytest.raises(module.TicketError, match='wait failed'):
        execute(fixture, runner)
    assert len([argv for argv, _ in runner.calls if argv[:3] == ['xcrun', 'notarytool', 'wait']]) == 1


def test_artifact_concurrency_is_sha_scoped_and_release_serialization_unchanged():
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/desktop.yml').read_text())
    assert workflow['concurrency']['cancel-in-progress'] is True
    assert workflow['concurrency']['group'] == "desktop-${{ github.ref }}${{ github.event_name == 'workflow_dispatch' && inputs.release_tag == '' && format('-artifact-{0}', github.sha) || '' }}"
    # Both event and empty-tag predicates are required for the SHA suffix;
    # pushes and nonempty manual releases retain desktop-<ref> serialization.


def test_real_nonwait_upload_envelope_requires_same_id_accepted_wait(fixture):
    envelope = {'id': SUBMISSION, 'message': 'Successfully uploaded file', 'path': str(fixture['package'])}
    runner = Commands(fixture, submit=[(0, json.dumps(envelope), '')])
    result = execute(fixture, runner)
    assert result['notarization_status'] == 'Accepted'
    assert [argv[:3] for argv, _ in runner.calls][1:4] == [
        ['xcrun', 'notarytool', 'submit'], ['xcrun', 'notarytool', 'wait'], ['xcrun', 'stapler', 'staple'],
    ]
    assert runner.calls[2][0][3] == SUBMISSION
    assert 'Successfully uploaded file' not in fixture['receipt'].read_text()


def test_nonwait_upload_id_alone_does_not_establish_accepted_ticket(fixture):
    runner = Commands(fixture, submit=[(0, json.dumps({'id': SUBMISSION, 'message': 'Successfully uploaded file'}), '')], wait=[(0, json.dumps({'id': SUBMISSION, 'status': 'In Progress'}), '')])
    with pytest.raises(module.TicketError, match='did not return Accepted'):
        execute(fixture, runner)
    assert not any(argv[:2] == ['xcrun', 'stapler'] for argv, _ in runner.calls)


def test_present_unknown_submission_status_is_not_pending(fixture):
    runner = Commands(fixture, submit=[(0, json.dumps({'id': SUBMISSION, 'status': 'Unexpected service status'}), '')])
    with pytest.raises(module.TicketError, match='unknown status'):
        execute(fixture, runner)
    assert len(runner.calls) == 2 and not fixture['receipt'].exists()
