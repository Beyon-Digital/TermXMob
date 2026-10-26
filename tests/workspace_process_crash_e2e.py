"""Opt-in, process-level abrupt-crash proof for the isolated TermX runtime.

Run only in an explicitly granted serialized test slot:
  PYTHONPATH=src python tests/workspace_process_crash_e2e.py OUTPUT_DIRECTORY

The controller owns two successive Python backend processes. It SIGKILLs only
the exact first Popen PID after a nonce/PID/config identity handshake, while
real parent/child tasks are active. Both processes use one synthetic SQLite
state directory, deterministic no-network inference, and no native engine.

The normal write_file executor performs a real scratch-file creation. A fixture
wrapper then fsyncs an observation journal and holds its return, so the actual
manager checkpoint is still `running` when the backend is killed. This makes
the recovery ambiguity real without leaving an orphan shell/process behind.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
from time import monotonic, sleep, time
import traceback
from fastapi import Request


COORDINATOR = 'CRASH_COORDINATOR_CASE'
READER = 'CRASH_READER_CASE'
WRITER = 'CRASH_AMBIGUOUS_WRITER_CASE'
PENDING = 'CRASH_PENDING_APPROVAL_CASE'
CANCELED = 'CRASH_CANCELED_CHILD_CASE'
FOLLOWUP = 'CRASH_DIRECT_FOLLOWUP_CASE'
CHILDREN = [
    ('Canceled worker', CANCELED, 'ask', 'crash-spawn-canceled'),
    ('Reader', READER, 'ask', 'crash-spawn-reader'),
    ('Ambiguous writer', WRITER, 'agent', 'crash-spawn-writer'),
    ('Pending reviewer', PENDING, 'agent', 'crash-spawn-pending'),
]
TERMINAL = {'completed', 'failed', 'cancelled'}


class Journal:
    def __init__(self, path, phase):
        self.path = Path(path)
        self.phase = phase
        self.lock = threading.Lock()

    def append(self, kind, **data):
        row = {'kind': kind, 'phase': self.phase, 'pid': os.getpid(), 'time': time(), **data}
        encoded = (json.dumps(row, sort_keys=True) + '\n').encode()
        with self.lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)
        return row


def journal_rows(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def worker(root: Path, port: int, phase: str):
    """A private fixture backend, never imported by the production application."""
    import uvicorn
    from fastapi import HTTPException
    from termx.agent.providers import ProviderCall, ProviderTurn
    from termx.agent.secrets import CredentialStore
    from termx.agent.tools.registry import ToolOutcome, ToolSpec, make_always
    from termx.app import AppState, create_app

    root = root.resolve()
    control = os.environ['TERMX_PROCESS_PROOF_CONTROL']
    nonce = os.environ['TERMX_PROCESS_PROOF_NONCE']
    os.environ.update(TERMX_CONFIG_DIR=str(root / 'config'), TERMX_AGENTS_DIR=str(root / 'agents'),
                     TERMX_ENGINE_STARTUP_REFRESH='0')
    journal = Journal(root / 'effects.jsonl', phase)

    class Adapter:
        async def test(self):
            raise AssertionError('This proof never tests a real provider connection')

        async def plan(self, prompt, cwd, manifest):
            journal.append('model.plan', role=role(prompt))
            return {'summary': 'Synthetic process-crash proof', 'steps': ['Inspect the synthetic task'],
                    'tools': [], 'risks': []}, 'crash-fixture-plan'

        async def turn(self, **kwargs):
            prompt = kwargs['prompt']
            selected = role(prompt)
            completed_calls = {str(item.get('call_id') or '') for item in (kwargs.get('input_items') or [])
                if isinstance(item, dict) and str(item.get('type') or '').endswith('call_output')}
            journal.append('model.turn', role=selected, completed_call_ids=sorted(completed_calls))
            if selected == COORDINATOR:
                if phase == 'before' and 'crash-spawn-pending' not in completed_calls:
                    calls = [ProviderCall(type='function', call_id=call_id, name='spawn_subagent',
                        arguments={'agent': name, 'task': task, 'mode': mode})
                        for name, task, mode, call_id in CHILDREN]
                    return ProviderTurn('crash-parent-spawn-response', '', calls, {}, [
                        {'type': 'function_call', 'call_id': call.call_id, 'name': call.name,
                         'arguments': json.dumps(call.arguments)} for call in calls])
                journal.append('coordinator.waiting')
                await asyncio.Event().wait()
            if selected in {READER, CANCELED}:
                journal.append('reader.waiting', role=selected)
                await asyncio.Event().wait()
            if selected == WRITER:
                if 'crash-real-write-once' in completed_calls:
                    return ProviderTurn('crash-writer-finished', 'Synthetic writer recovery settled.', [], {}, [])
                call = ProviderCall(type='function', call_id='crash-real-write-once', name='write_file',
                    arguments={'path': 'crash-effect.txt', 'content': 'Synthetic crash proof\n', 'expected_revision': None})
                return ProviderTurn('crash-writer-tool-response', '', [call], {}, [
                    {'type': 'function_call', 'call_id': call.call_id, 'name': call.name,
                     'arguments': json.dumps(call.arguments)}])
            if selected == PENDING:
                call = ProviderCall(type='function', call_id='crash-private-pending-once',
                    name='process_proof_pending', arguments={})
                return ProviderTurn('crash-pending-tool-response', '', [call], {}, [
                    {'type': 'function_call', 'call_id': call.call_id, 'name': call.name, 'arguments': '{}'}])
            if selected == FOLLOWUP:
                return ProviderTurn('crash-followup-finished', 'Explicit synthetic follow-up completed.', [], {}, [])
            raise AssertionError('Unexpected synthetic task role: ' + selected)

    def role(prompt):
        # Isolation appends explanatory text to child prompts. Choose the last
        # explicit fixture marker, after any earlier main coordination context.
        found = [(str(prompt).rfind(value), value) for value in
                 [COORDINATOR, READER, WRITER, PENDING, CANCELED, FOLLOWUP]]
        position, value = max(found)
        return value if position >= 0 else 'unknown'

    adapter = Adapter()
    state = AppState(passcode=None, credentials=CredentialStore(memory={'crash-fixture': 'synthetic-fixture-only'}),
                     adapter_factory=lambda *_: adapter)
    if not state.identity.configured:
        state.identity.setup_owner('process-proof', 'synthetic-process-password-2026')
    if state.agent_store.get_provider('crash-fixture') is None:
        state.agent.save_provider(provider_id='crash-fixture', kind='openai-compatible',
            name='No-network process fixture', base_url='http://127.0.0.1:1/v1', model='fixture',
            capabilities=['shell', 'functions'], api_key='synthetic-fixture-only')
    original_write = state.agent._tools.get('write_file')
    assert original_write is not None

    async def held_write(call, ctx):
        # The production executor checks the real managed scratch root and
        # create-exclusive/revision rules before this test-only suspension.
        outcome = await original_write.execute(call, ctx)
        journal.append('write.executor_returned', task_id=ctx.task_id, call_id=call.call_id,
            cwd=ctx.cwd, ok=bool((outcome.result or {}).get('ok')), result=outcome.result)
        marker = root / ('release-write-' + ctx.task_id)
        while not marker.exists():
            await asyncio.sleep(.025)
        return outcome

    async def pending_effect(call, ctx):
        journal.append('pending.executor_started', task_id=ctx.task_id, call_id=call.call_id)
        return ToolOutcome({'ok': True, 'fixture_only': True})

    state.agent._tools.register(dataclasses.replace(original_write, execute=held_write))
    state.agent._tools.register(ToolSpec(name='process_proof_pending',
        description='Synthetic no-network private-request boundary for process recovery proof.',
        parameters={'type': 'object', 'properties': {}, 'additionalProperties': False},
        mutability='write', parallel_safe=False, approval='always', expose_read_only=False,
        execute=pending_effect, decide=make_always('Synthetic action needs review', 'Exercise only the owned fixture journal')))
    app = create_app(state)

    @app.get('/_process_proof/state')
    async def inspect(request: Request):
        if not secrets.compare_digest(request.headers.get('X-Termx-Process-Proof', ''), control):
            raise HTTPException(403, 'Fixture inspection requires its controller')
        tasks = state.agent_store.list_tasks(limit=100)
        return {'pid': os.getpid(), 'nonce': nonce, 'root': str(root), 'phase': phase,
            'live_workers': [key for key, value in state.agent._workers.items() if not value.done()],
            # Observe real broker receipts without returning their private
            # action arguments, envelopes or executable single-use permits.
            'broker_reviews': {row['id']: {**{key: row.get(key) for key in
                ['id', 'fingerprint', 'status', 'reason', 'outcome', 'run_id', 'tool_id', 'decision', 'decision_source']},
                'permit_present': 'permit' in row}
                for row in state.browser.records.list('review')},
            'tasks': {task['id']: {'status': task['status'], 'parent_id': task.get('parent_id'),
                'engine': task.get('engine'), 'error': task.get('error'), 'cwd': task['cwd'],
                'recovery_requires_fresh_instruction':
                    (task.get('runtime') or {}).get('recovery_requires_fresh_instruction'),
                'checkpoint': slim(state.agent_store.latest_checkpoint(task['id'], kind='execution')),
                'approvals': [{'id': a['id'], 'kind': a['kind'], 'status': a['status'],
                    'call_id': (a.get('payload', {}).get('call') or {}).get('call_id')}
                    for a in state.agent_store.approvals(task['id'])]}
                for task in tasks}}

    def slim(checkpoint):
        if not checkpoint:
            return None
        value = {key: checkpoint.get(key) for key in ['id', 'side_effect_state', 'pending_call_id']}
        reviewed = (checkpoint.get('payload') or {}).get('action_review')
        value['action_review'] = {key: reviewed.get(key) for key in ['id', 'fingerprint']} if reviewed else None
        return value

    # create_app registers its SPA catch-all last. Fixture inspection must
    # precede it; changing only this route's URL would still hit the catch-all.
    inspection_route = app.router.routes.pop()
    assert inspection_route.path == '/_process_proof/state'
    app.router.routes.insert(0, inspection_route)

    journal.append('worker.started', nonce_hash=hashlib.sha256(nonce.encode()).hexdigest())
    try:
        uvicorn.run(app, host='127.0.0.1', port=port, log_level='error', access_log=False,
                    timeout_graceful_shutdown=5)
    finally:
        journal.append('worker.graceful_exit')


def run(destination):
    import httpx

    started_at = time()
    scenario_deadline = monotonic() + 180
    if os.name != 'posix':
        raise RuntimeError('This proof requires actual POSIX SIGKILL semantics')
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'fixture-state'
    root.mkdir()
    journal_path = root / 'effects.jsonl'
    report = {'status': 'running', 'proof': 'actual-SIGKILL-and-new-backend-process', 'checks': [],
              'http': [], 'processes': [], 'native_engines_tested': False, 'external_provider_calls': 0,
              'boundaries': {'model': 'deterministic no-network adapter',
                  'write': 'production write_file executor held after real scratch creation by fixture wrapper',
                  'telemetry': 'fixture-only authenticated worker/PID/checkpoint inspection'}}
    process = log = client = None
    control = secrets.token_urlsafe(24)
    current_nonce = None
    current_phase = None
    source_root = Path(__file__).resolve().parents[1]
    # Closing this unused reservation never touches an existing listener. A
    # bind race fails closed; the controller never kills another port owner.
    reservation = socket.socket()
    reservation.bind(('127.0.0.1', 0))
    port = reservation.getsockname()[1]
    reservation.close()
    base = 'http://127.0.0.1:' + str(port)
    report['loopback_port'] = port

    def checkpoint(name, **details):
        row = {'stage': name, 'time': time(), **details}
        report.setdefault('stages', []).append(row)
        print(json.dumps(row), flush=True)

    def api(method, path, body=None, *, allowed=(200,)):
        if monotonic() >= scenario_deadline:
            raise TimeoutError('The bounded 180-second crash scenario deadline expired')
        response = client.request(method, path, json=body)
        report['http'].append({'method': method, 'path': path, 'status': response.status_code})
        if response.status_code not in allowed:
            raise AssertionError(f'{method} {path}: {response.status_code} {response.text[:1500]}')
        return response

    def inspection():
        response = client.get('/_process_proof/state', headers={'X-Termx-Process-Proof': control}, timeout=2)
        response.raise_for_status()
        value = response.json()
        assert value['pid'] == process.pid and value['nonce'] == current_nonce
        assert value['root'] == str(root) and value['phase'] == current_phase
        return value

    def wait(predicate, message, timeout=25):
        deadline = min(scenario_deadline, monotonic() + timeout)
        while True:
            if process.poll() is not None:
                raise AssertionError(f'Owned backend {process.pid} exited {process.returncode}: {message}')
            value = predicate()
            if value:
                return value
            if monotonic() >= deadline:
                raise AssertionError(message)
            sleep(.04)

    def start(phase):
        nonlocal process, log, current_nonce, current_phase
        current_nonce, current_phase = secrets.token_urlsafe(24), phase
        env = dict(os.environ)
        env.update(PYTHONPATH=str(source_root / 'src'), TERMX_PROCESS_PROOF_CONTROL=control,
                   TERMX_PROCESS_PROOF_NONCE=current_nonce, TERMX_ENGINE_STARTUP_REFRESH='0')
        log = (output / ('backend-' + phase + '.log')).open('wb')
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker',
            str(root), '--port', str(port), '--phase', phase], cwd=source_root, env=env,
            stdout=log, stderr=subprocess.STDOUT)
        report['processes'].append({'pid': process.pid, 'phase': phase, 'start': time(),
                                    'nonce_sha256': hashlib.sha256(current_nonce.encode()).hexdigest()})
        deadline = min(scenario_deadline, monotonic() + 30)
        while True:
            if process.poll() is not None:
                raise AssertionError(f'Owned {phase} backend exited during startup ({process.returncode})')
            try:
                value = inspection()
                break
            except (httpx.TransportError, httpx.HTTPStatusError):
                if monotonic() > deadline:
                    raise AssertionError('Private fixture backend startup timed out')
                sleep(.05)
        checkpoint('backend-' + phase + '-ready', pid=value['pid'])

    def stop_owned():
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def task(identifier):
        # Workspace REST task reads intentionally expose the supervision tree,
        # not approvals. Use the actual authorized GraphQL detail contract,
        # including terminal approvals needed for the post-crash identity check.
        query = '''query ProcessProofTask($id: String!) {
          agent_task(task_id: $id) {
            id status prompt cwd engine parent_id error result approvals
          }
        }'''
        response = api('POST', '/graphql', {'query': query, 'variables': {'id': identifier}}).json()
        assert not response.get('errors'), response
        value = response['data']['agent_task']
        assert value is not None and value['id'] == identifier, response
        return value

    def pending(identifier, call_id=None, kind=None):
        return next((a for a in task(identifier).get('approvals', []) if a['status'] == 'pending'
            and (kind is None or a['kind'] == kind)
            and (call_id is None or (a.get('payload', {}).get('call') or {}).get('call_id') == call_id)), None)

    def approve(identifier, approval):
        response = api('POST', f'/api/workspace/tasks/{identifier}/approvals/{approval["id"]}/resolve',
                       {'decision': 'approved'}, allowed=(200, 400, 409))
        value = response.json()
        if response.status_code == 200:
            return value
        # The coordinator may approve its child's plan between this fixture's
        # read and click. Verify that exact durable decision; never accept a
        # failed action approval, denial, changed request or permission error.
        if approval['kind'] == 'plan' and value.get('detail') == 'approval is already resolved':
            current = next((a for a in task(identifier)['approvals'] if a['id'] == approval['id']), None)
            if current and current['task_id'] == identifier and current['status'] == 'approved':
                report.setdefault('approved_plan_races', []).append({'task_id': identifier, 'approval_id': approval['id']})
                return current
        raise AssertionError(f'Exact approval was not confirmed: {response.status_code} {value}')

    def snapshot():
        return api('GET', '/api/workspace/orchestrator').json()

    def child_named(name):
        return next((c for c in snapshot()['children'] if c['title'] == name), None)

    def effects(kind='write.executor_returned'):
        return [row for row in journal_rows(journal_path) if row['kind'] == kind]

    def assert_no_phantom_running(value):
        phantom = [key for key, row in value['tasks'].items()
                   if row['status'] == 'running' and key not in value['live_workers']]
        assert not phantom, {'phantom_running': phantom, 'live_workers': value['live_workers']}

    try:
        client = httpx.Client(base_url=base, timeout=10, trust_env=False)
        start('before')
        response = api('POST', '/auth/login', {'username': 'process-proof',
            'password': 'synthetic-process-password-2026', 'transport': 'bearer', 'device_name': 'Process crash proof'})
        credentials = response.json()
        client.headers['Authorization'] = 'Bearer ' + credentials['access_token']
        primary = api('POST', '/api/workspace/orchestrator/ensure', {}).json()['session']
        parent_body = {'prompt': COORDINATOR, 'request_id': 'process-proof-parent-once',
            'limits': {'max_steps': 64, 'max_seconds': 180, 'max_parallel_subagents': 3, 'max_subagents_total': 8}}
        parent = api('POST', '/api/workspace/sessions/' + primary['id'] + '/turns', parent_body).json()
        parent_id = parent['id']
        approve(parent_id, wait(lambda: pending(parent_id, kind='plan'), 'Parent plan approval missing'))
        admitted = {}
        for name, marker, mode, call_id in CHILDREN:
            approve(parent_id, wait(lambda cid=call_id: pending(parent_id, cid, 'tool'), 'Delegation approval missing: ' + name))
            child = wait(lambda label=name: child_named(label), 'Child not enrolled: ' + name)
            admitted[name] = child
            if name == 'Canceled worker':
                wait(lambda: task(child['task_id'])['status'] == 'running', 'Canceled fixture never ran')
                api('POST', '/api/workspace/tasks/' + child['task_id'] + '/cancel', {})
                wait(lambda: task(child['task_id'])['status'] == 'cancelled', 'Original cancellation did not settle')
            elif name == 'Reader':
                wait(lambda: task(child['task_id'])['status'] == 'running', 'Reader did not start')
                updated = api('POST', '/api/workspace/orchestrator/children/' + child['session_id'] + '/control',
                    {'owner': 'user', 'revision': child['revision']}).json()
                assert updated['control_owner'] == 'user'
            elif name == 'Ambiguous writer':
                # Agent plans can be auto-approved through the existing parent
                # relay. Any consequential tool gate still requires a decision.
                deadline = monotonic() + 20
                while not effects():
                    request = pending(child['task_id'])
                    if request:
                        approve(child['task_id'], request)
                    if monotonic() > deadline:
                        raise AssertionError('Real scratch write did not reach held return')
                    sleep(.04)
            else:
                wait(lambda: pending(child['task_id'], 'crash-private-pending-once', 'tool'),
                     'Private pending child action missing')
        wait(lambda: task(parent_id)['status'] in {'running', 'awaiting_approval', 'paused'},
             'Coordinator did not remain active at the crash boundary')
        before = inspection()
        assert_no_phantom_running(before)
        writer_id = admitted['Ambiguous writer']['task_id']
        reader_id = admitted['Reader']['task_id']
        pending_id = admitted['Pending reviewer']['task_id']
        canceled_id = admitted['Canceled worker']['task_id']
        write_checkpoint = before['tasks'][writer_id]['checkpoint']
        assert write_checkpoint is not None and write_checkpoint['side_effect_state'] == 'running'
        reviewed = write_checkpoint['action_review']
        assert reviewed and reviewed['id'] == writer_id + ':crash-real-write-once'
        assert isinstance(reviewed['fingerprint'], str) and reviewed['fingerprint']
        write_review_id = reviewed['id']
        broker_before = before['broker_reviews'][write_review_id]
        assert broker_before['fingerprint'] == reviewed['fingerprint']
        assert broker_before['run_id'] == writer_id and broker_before['tool_id'] == 'agent.write_file'
        assert broker_before['status'] == 'executing' and not broker_before['permit_present']
        writer_review_ids = {key for key, value in before['broker_reviews'].items() if value['run_id'] == writer_id}
        assert len(effects()) == 1 and effects()[0]['ok'] is True
        write_path = Path(effects()[0]['cwd']) / 'crash-effect.txt'
        assert write_path.resolve().is_relative_to(root.resolve()) and write_path.read_text() == 'Synthetic crash proof\n'
        pending_before = pending(pending_id, 'crash-private-pending-once', 'tool')
        report['before_crash'] = before
        report['originals'] = {'primary': primary['id'], 'parent': parent_id, 'children': admitted,
                               'pending_approval_id': pending_before['id']}
        checkpoint('actual-in-flight-state-ready', parent=parent_id, active_workers=len(before['live_workers']))
        # No port lookup, process-name matching, process group or broad kill.
        identity = inspection()
        assert process.poll() is None and identity['pid'] == process.pid
        crashed_pid = process.pid
        process.kill()
        process.wait(timeout=5)
        assert process.returncode == -signal.SIGKILL
        report['abrupt_exit'] = {'pid': crashed_pid, 'signal': 'SIGKILL', 'returncode': process.returncode,
            'identity_verified': True, 'graceful_shutdown_executed': any(row['kind'] == 'worker.graceful_exit'
                and row['phase'] == 'before' for row in journal_rows(journal_path))}
        assert not report['abrupt_exit']['graceful_shutdown_executed']
        log.close()
        checkpoint('exact-owned-backend-sigkilled', pid=crashed_pid)
        start('after')
        assert api('GET', '/auth/me').json()['session_id'] == credentials['session_id']
        recovered = snapshot()
        assert recovered['session']['id'] == primary['id']
        assert {c['session_id'] for c in recovered['children']} == {c['session_id'] for c in admitted.values()}
        by_id = {c['task_id']: c for c in recovered['children']}
        assert by_id[reader_id]['control_owner'] == 'user'
        assert task(canceled_id)['status'] == 'cancelled'
        after = inspection()
        assert_no_phantom_running(after)
        interrupted = after['tasks'][writer_id]
        assert interrupted['status'] == 'failed'
        assert interrupted['recovery_requires_fresh_instruction'] == {
            'checkpoint_id': write_checkpoint['id'], 'review_id': write_review_id}
        assert 'fresh instruction' in interrupted['error'].lower()
        assert after['broker_reviews'][write_review_id]['status'] == 'invalidated'
        assert 'outcome may be unknown' in after['broker_reviews'][write_review_id]['reason']
        assert not after['broker_reviews'][write_review_id]['permit_present']
        assert writer_id not in after['live_workers']
        assert len(effects()) == 1
        report['after_crash'] = after
        report['checks'].append('SIGKILL while actual parent/children run preserves auth, primary, four links, user ownership and original canceled terminal without phantom workers')
        # Repeating the original HTTP receipt cannot recreate the coordinator
        # or any delegate, regardless of its post-crash recovery classification.
        receipt = api('POST', '/api/workspace/sessions/' + primary['id'] + '/turns', parent_body).json()
        assert receipt['id'] == parent_id
        assert {c['session_id'] for c in snapshot()['children']} == {c['session_id'] for c in admitted.values()}
        report['checks'].append('Original parent request receipt returns the same task and delegates after process restart')
        recovery_responses = []
        for confirmed in (False, True, True):
            blocked = api('POST', '/api/workspace/tasks/' + writer_id + '/recover', {'confirm': confirmed}).json()
            assert blocked['id'] == writer_id and blocked['status'] == 'failed'
            assert 'fresh instruction' in blocked['error'].lower()
            observed = inspection()
            assert writer_id not in observed['live_workers'] and len(effects()) == 1
            assert write_path.read_text() == 'Synthetic crash proof\n'
            assert observed['tasks'][writer_id]['checkpoint'] == write_checkpoint
            assert {key for key, value in observed['broker_reviews'].items() if value['run_id'] == writer_id} == writer_review_ids
            assert observed['broker_reviews'][write_review_id]['status'] == 'invalidated'
            assert not observed['broker_reviews'][write_review_id]['permit_present']
            recovery_responses.append({'confirm': confirmed, 'http_status': 200,
                                       'task_id': blocked['id'], 'status': blocked['status'], 'error': blocked['error']})
        report['checks'].append('Executing broker receipt is invalidated after SIGKILL; startup and repeated unconfirmed/confirmed recovery keep one write attempt, no permit or worker and require a fresh instruction')
        report['broker_review_evidence'] = {'review_id': write_review_id, 'checkpoint': write_checkpoint,
            'before': broker_before, 'after': after['broker_reviews'][write_review_id],
            'recovery_responses': recovery_responses}
        # The original private approval is intentionally not reconstructed from
        # a redacted public payload. Its exact identity remains inspectable.
        original_approval = next(a for a in task(pending_id)['approvals'] if a['id'] == pending_before['id'])
        assert original_approval['status'] == 'denied', original_approval
        old_approval = api('POST', f'/api/workspace/tasks/{pending_id}/approvals/{pending_before["id"]}/resolve',
            {'decision': 'approved'}, allowed=(400, 409))
        assert not effects('pending.executor_started')
        assert task(pending_id)['status'] == 'failed' and task(pending_id).get('error')
        report['checks'].append('Lost private pending request keeps its approval ID, becomes denied with interrupted-task feedback and cannot execute from public metadata')
        # A direct fresh instruction has its own durable receipt and preserves
        # explicit user ownership, rather than silently retrying a lost turn.
        # The ambiguous writer is already terminal-failed, so no Stop is needed.
        reader_session = admitted['Reader']['session_id']
        followup = {'prompt': FOLLOWUP, 'request_id': 'process-proof-direct-followup-once'}
        direct = api('POST', '/api/workspace/sessions/' + reader_session + '/turns', followup).json()
        wait(lambda: task(direct['id'])['status'] == 'completed', 'Fresh direct instruction did not finish')
        repeated = api('POST', '/api/workspace/sessions/' + reader_session + '/turns', followup).json()
        assert repeated['id'] == direct['id'] and child_named('Reader')['control_owner'] == 'user'
        api('POST', '/api/workspace/tasks/' + parent_id + '/cancel', {})
        wait(lambda: all(t['status'] in TERMINAL for t in inspection()['tasks'].values()),
             'Explicit final cancellation left active task state')
        final = inspection()
        assert_no_phantom_running(final)
        assert not final['live_workers']
        assert task(canceled_id)['status'] == 'cancelled'
        assert len(effects()) == 1 and effects()[0]['ok'] is True
        assert final['broker_reviews'][write_review_id]['status'] == 'invalidated'
        assert not final['broker_reviews'][write_review_id]['permit_present']
        assert not effects('pending.executor_started')
        activity = snapshot()['activity']
        assert len({event['id'] for event in activity}) == len(activity)
        report['checks'].append('Explicit fresh direct instruction deduplicates its receipt; final cancel leaves all workers terminal, original cancellation unchanged and report events unique')
        report['final_state'] = final
        report['write_evidence'] = {'attempts': len(effects()), 'successful_creations': 1,
            'sha256': hashlib.sha256(write_path.read_bytes()).hexdigest(),
            'broker_bound_action_replayed': False, 'recovery_confirmation_is_new_action_consent': False}
        report['status'] = 'passed'
        checkpoint('process-crash-proof-passed', checks=len(report['checks']))
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        report['traceback'] = traceback.format_exc()
        if process is not None and process.poll() is None and client is not None:
            try:
                report['failure_state'] = inspection()
            except Exception:
                pass
        raise
    finally:
        stop_owned()
        if log is not None:
            log.close()
        if client is not None:
            client.close()
        report['cleanup'] = {'owned_backend_stopped': process is None or process.poll() is not None,
                             'last_pid': process.pid if process else None,
                             'last_returncode': process.returncode if process else None}
        report['journal'] = journal_rows(journal_path)
        report['duration_seconds'] = round(time() - started_at, 2)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print('Report: ' + str(output / 'report.json'), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', nargs='?')
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('--phase', choices=['before', 'after'])
    args = parser.parse_args()
    if args.worker:
        if args.port is None or args.phase is None:
            parser.error('--worker requires --port and --phase')
        worker(args.worker, args.port, args.phase)
    elif args.destination:
        run(args.destination)
    else:
        parser.error('An explicit fresh output directory is required')
