"""Opt-in real daemon restart/scheduler acceptance; never calls an AI model.

Run: python tests/workspace_autonomy_e2e.py /path/to/evidence
Uses fresh temporary state, real shell subprocesses and normal app lifespan.
Agent-mode inference is replaced by a clearly labelled deterministic adapter.
The isolated Agent fixture uses HostSandboxRunner; this does not qualify OS
sandbox enforcement. Scheduled commands already use the host execution profile.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

REPO = Path(__file__).resolve().parents[1]


def daemon(ROOT: Path, port: int):
    import uvicorn
    os.environ.update(TERMX_CONFIG_DIR=str(ROOT/'config'), TERMX_AGENTS_DIR=str(ROOT/'agents'), TERMX_ENGINE_STARTUP_REFRESH='0', TERMX_AI_SYNTHETIC_API_KEY='fixture-not-provider-secret')
    sys.path.insert(0, str(REPO/'tests'))
    from test_agent import FakeAdapter
    from termx.app import AppState, create_app
    from termx.sandbox.host import HostSandboxRunner
    adapter = FakeAdapter('printf synthetic-agent-ok')
    state = AppState(passcode=None, adapter_factory=lambda *_: adapter)
    state.agent._runner_for = lambda profile, **kw: HostSandboxRunner(profile=profile)
    app = create_app(state)
    bootstrap = ROOT/'bootstrap.json'
    if not bootstrap.exists():
        owner = state.identity.setup_owner('live-owner', 'fixture-password-only-123')
        fixture = ROOT/'fixture'
        fixture.mkdir(exist_ok=True)
        project = state.projects.register(str(fixture), name='Autonomy fixture')
        state.agent.save_provider(provider_id='synthetic', kind='openai-compatible', name='Synthetic proof provider',
            base_url='http://127.0.0.1:9999/v1', model='synthetic', capabilities=['shell'])
        session = state.workspace.create_session(owner, title='Autonomy acceptance', project_id=project['id'],
            cwd=str(fixture), provider_id='synthetic', mode='agent')
        bootstrap.write_text(json.dumps({'owner':owner.id, 'conversation_id':session['id'], 'project_id':project['id']}))
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info', lifespan='on')


def exercise(ROOT: Path):
    sock=socket.socket();sock.bind(('127.0.0.1',0));PORT=sock.getsockname()[1];sock.close()
    URL=f'http://127.0.0.1:{PORT}'
    process=None
    checks=[]


    def start():
        nonlocal process
        log=(ROOT/'daemon.log').open('a')
        env={**os.environ,'PYTHONPATH':str(REPO/'src')}
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--daemon',str(ROOT),str(PORT)],cwd=REPO,env=env,stdout=log,stderr=log)
        log.close()
        until=time.monotonic()+20
        while time.monotonic()<until:
            if process.poll() is not None:raise RuntimeError('daemon exited; see daemon.log')
            try:
                if httpx.get(URL+'/auth/methods',timeout=1).status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.1)
        raise RuntimeError('daemon startup timeout')


    def stop(hard=False):
        nonlocal process
        if process and process.poll() is None:
            process.kill() if hard else process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)


    def client():
        c=httpx.Client(base_url=URL,timeout=10)
        r=c.post('/auth/login',json={'username':'live-owner','password':'fixture-password-only-123','transport':'bearer'})
        assert r.status_code==200,r.text
        c.headers['Authorization']='Bearer '+r.json()['access_token']
        return c


    def api(c,method,path,body=None):
        r=c.request(method,'/api/workspace'+path,json=body)
        assert r.status_code<300,(r.status_code,r.text,path)
        return r.json()


    def create(c,label,command=None,at=None):
        seed=json.loads((ROOT/'bootstrap.json').read_text())
        body={'request_id':'proof-'+label+'-'+uuid.uuid4().hex,'conversation_id':seed['conversation_id'],
            'success_criteria':label+' completes once','max_runs':1,'limits':{'max_steps':8,'max_seconds':120,'shell_timeout_s':100},
            'prompt':label,'spec':{'kind':'once','at':at or time.time()+3},'grant_days':1}
        if command:body['command']=command
        return api(c,'POST','/automations',body)


    def records(kind):
        with sqlite3.connect(ROOT/'config/workspace.sqlite3') as db:
            db.row_factory=sqlite3.Row
            return [{**json.loads(r['body']),'id':r['id'],'revision':r['revision']} for r in db.execute('SELECT * FROM records WHERE kind=?',(kind,))]


    def wait_run(schedule,statuses,timeout=25):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            found=[r for r in records('schedule_run') if r['schedule_id']==schedule['id']]
            if found and found[-1]['status'] in statuses:return found[-1]
            time.sleep(.15)
        raise AssertionError(('scheduled run timeout',schedule['id'],records('schedule_run')))


    def marker():
        path=ROOT/'fixture/marker.txt'
        return path.read_text().splitlines() if path.exists() else []

    try:
        start()
        c=client()
        now=time.time()
        first=create(c,'first',"printf 'first\\n' >> marker.txt; printf first-output",at=now+3)
        second=create(c,'second',"printf 'second\\n' >> marker.txt; printf second-output",at=now+16)
        c.close() # No UI or connected HTTP client during execution.
        one=wait_run(first,{'completed'})
        checks.append({'check':'daemon executes authorized shell while clients disconnected','passed':marker()==['first']})
        stop(hard=True)
        start()
        two=wait_run(second,{'completed'})
        checks.append({'check':'real SIGKILL/restart preserves second due run and first exactly once','passed':marker()==['first','second']})
        c=client()
        snapshot=api(c,'GET','/automations')
        checks.append({'check':'reconnected remote API sees durable completed command output','passed':any('first-output' in str(r.get('step_results')) for r in snapshot['schedule_run'])})
        # Persist a claimed dispatch with no enrolled task ID to emulate the exact
        # ambiguous crash window; the actual restart must quarantine rather than replay.
        ambiguous=create(c,'ambiguous',"printf 'AMBIGUOUS\\n' >> marker.txt",at=time.time()+120)
        paused=api(c,'PATCH','/schedules/'+ambiguous['id'],{'revision':ambiguous['revision'],'enabled':False})
        stop(hard=True)
        with sqlite3.connect(ROOT/'config/workspace.sqlite3') as db:
            row=db.execute('SELECT owner,project FROM records WHERE kind=? AND id=?',('schedule',ambiguous['id'])).fetchone()
            body={'schedule_id':ambiguous['id'],'goal_id':ambiguous['goal_id'],'status':'dispatching','due':time.time()}
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)',('schedule_run','ambiguous-proof',row[0],row[1],1,json.dumps(body),time.time(),time.time()))
        start()
        quarantined=wait_run(ambiguous,{'needs_attention'})
        checks.append({'check':'actual restart quarantines unknown dispatch without blind replay','passed':'restarted' in quarantined.get('reason','').lower() and marker()==['first','second']})
        c.close();c=client()
        agent=create(c,'synthetic-agent')
        c.close()
        agent_run=wait_run(agent,{'completed'})
        with sqlite3.connect(ROOT/'config/agent.sqlite3') as db:
            db.row_factory=sqlite3.Row
            task=dict(db.execute('SELECT * FROM tasks WHERE id=?',(agent_run['task_id'],)).fetchone())
            events=[dict(r) for r in db.execute('SELECT * FROM events WHERE task_id=?',(agent_run['task_id'],))]
        checks.append({'check':'synthetic Agent schedule auto-starts initial plan and executes real shell tool','passed':'Verified successfully' in (task.get('result') or '') and 'synthetic-agent-ok' in str(events),'provider':'synthetic deterministic adapter; no real model'})
        (ROOT/'synthetic-agent-events.json').write_text(json.dumps(events,indent=2))
        c=client()
        paused_schedule=create(c,'paused',"printf 'PAUSED\\n' >> marker.txt",at=time.time()+3)
        api(c,'PATCH','/schedules/'+paused_schedule['id'],{'revision':paused_schedule['revision'],'enabled':False})
        c.close();time.sleep(6)
        checks.append({'check':'remote pause prevents future scheduled launch','passed':not any(r['schedule_id']==paused_schedule['id'] for r in records('schedule_run')) and 'PAUSED' not in marker()})
        c=client()
        active=create(c,'revoke-active',"printf 'active\\n' >> marker.txt; sleep 40; printf 'LATE\\n' >> marker.txt",at=time.time()+3)
        c.close()
        active_run=wait_run(active,{'running'})
        until=time.monotonic()+5
        while 'active' not in marker() and time.monotonic()<until:time.sleep(.1)
        c=client();api(c,'DELETE','/delegations/'+active['grant_id']);c.close()
        revoked=wait_run(active,{'cancelled'},timeout=15)
        checks.append({'check':'remote delegation revocation cancels active real process','passed':'active' in marker() and 'LATE' not in marker() and revoked['status']=='cancelled'})
        c=client()
        active_stop=create(c,'stop-active',"printf 'stop-start\\n' >> marker.txt; sleep 40; printf 'STOP-LATE\\n' >> marker.txt",at=time.time()+3)
        c.close()
        stop_run=wait_run(active_stop,{'running'})
        c=client();api(c,'POST','/schedule-runs/'+stop_run['id']+'/stop');c.close()
        stopped=wait_run(active_stop,{'cancelled'},timeout=15)
        checks.append({'check':'remote stop cancels active scheduled process','passed':stopped['status']=='cancelled' and 'STOP-LATE' not in marker()})
        (ROOT/'ledger.json').write_text(json.dumps({kind:records(kind) for kind in ['schedule','schedule_run','delegation','goal']},indent=2))
        (ROOT/'report.json').write_text(json.dumps({'scope':'real daemon subprocess, scheduler lifespan, durable SQLite, actual shell processes; deterministic synthetic provider only for Agent mode','agent_fixture_sandbox':'HostSandboxRunner; OS sandbox enforcement not qualified','checks':checks,'marker':marker()},indent=2))
        assert all(x['passed'] for x in checks),checks
        print(json.dumps(checks,indent=2))
    finally:
        stop()


def run(destination: Path):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='termx-autonomy-proof-') as temporary:
        root = Path(temporary)
        try:
            exercise(root)
        finally:
            # Keep reviewable output even after an assertion or host error.
            # Private authentication databases stay in the isolated temporary dir.
            for path in root.iterdir():
                if path.is_file():
                    shutil.copy2(path, destination / path.name)
            marker = root / 'fixture' / 'marker.txt'
            if marker.exists():
                fixture = destination / 'fixture'
                fixture.mkdir(exist_ok=True)
                shutil.copy2(marker, fixture / 'marker.txt')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--daemon':
        daemon(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('destination', type=Path, help='Directory for retained proof artifacts')
        arguments = parser.parse_args()
        run(arguments.destination)
