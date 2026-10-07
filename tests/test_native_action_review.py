import asyncio
from time import time

import pytest

from termx.auto_review import ReviewVerdict
from termx.browser.service import BrowserService
from termx.engines.types import EffectiveRunConfiguration


def authority(service):
    service.records.put('agent-task-authority', 'managed-task', {'id': 'managed-task', 'principal_id': 'owner', 'session_id': 'sid', 'project_id': 'project', 'policy_version': 1})


def test_codex_stdio_approval_uses_broker_single_use_and_revoked_session_declines(tmp_path):
    from test_engines import _codex
    async def run():
        live = [True]; service = BrowserService(tmp_path / 'browser', session_valid=lambda *args: live[0]); authority(service)
        events = []; approvals = []
        engine = _codex(tmp_path, events, approvals, {'FAKE_CODEX_APPROVE_WHEN': 'please'})
        engine.browser_service = service
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(engine='codex', cwd=str(tmp_path), tools={'managed_task_id': 'managed-task'}))
            await engine.send(binding, 'please run the fixture')
            async with asyncio.timeout(10):
                while not approvals: await asyncio.sleep(.02)
            _, token, method, params, _ = approvals[0]
            assert method == 'browser.review' and params['browser_review']['effect'] == 'unknown'
            assert service.records.list('review')[0]['status'] == 'needs_user'
            live[0] = False
            await engine.respond_approval(binding, token, 'approve')
            async with asyncio.timeout(10):
                while not any(event.type == 'engine.turn.completed' for _, event in events): await asyncio.sleep(.02)
            assert service.records.list('review')[0]['status'] == 'blocked'
            assert not engine._review_items
        finally: await engine.shutdown()
    asyncio.run(run())


def test_acp_host_write_executes_real_file_once_and_protected_read_is_denied(tmp_path):
    from test_engines_acp import _engine
    async def run():
        service = BrowserService(tmp_path / 'browser', session_valid=lambda *args: True); authority(service)
        class Reviewer:
            version = 'qualified-small-v1'
            async def evaluate(self, action, context): return ReviewVerdict('ALLOW', 'typed_edit', 'Scoped edit', self.version, time() + 15)
        service.review.reviewer = Reviewer()
        engine = _engine([], []); engine.browser_service = service
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), tools={'managed_task_id': 'managed-task'}))
            client = engine._sessions[binding.binding_id]['client']
            await client.write_text_file(binding.native_session_id, 'result.txt', 'Actual brokered ACP file effect')
            assert (tmp_path / 'result.txt').read_text() == 'Actual brokered ACP file effect'
            assert len([row for row in service.records.list('review') if row['status'] == 'completed']) == 1
            (tmp_path / '.env').write_text('SECRET=do-not-observe')
            with pytest.raises(PermissionError): await client.read_text_file(binding.native_session_id, '.env')
        finally: await engine.shutdown()
    asyncio.run(run())


def test_acp_terminal_actual_effect_waits_for_exact_review_and_records_completion(tmp_path):
    from test_engines_acp import _engine
    import sys
    async def run():
        approvals = []; service = BrowserService(tmp_path / 'browser', session_valid=lambda *args: True); authority(service)
        engine = _engine([], approvals); engine.browser_service = service
        try:
            binding = await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path), tools={'managed_task_id': 'managed-task'}))
            client = engine._sessions[binding.binding_id]['client']
            effect = asyncio.create_task(client.create_terminal(binding.native_session_id, sys.executable, ['-c', 'from pathlib import Path; Path("effect.txt").write_text("once")']))
            async with asyncio.timeout(10):
                while not approvals: await asyncio.sleep(.02)
            assert not (tmp_path / 'effect.txt').exists()
            token, method, _, _ = approvals[0]; assert method == 'browser.review'
            await engine.respond_approval(binding, token, 'approve')
            terminal = await effect
            await client.wait_for_terminal_exit(binding.native_session_id, terminal.terminal_id)
            assert (tmp_path / 'effect.txt').read_text() == 'once'
            assert service.records.list('review')[0]['status'] == 'completed'
        finally: await engine.shutdown()
    asyncio.run(run())

def test_acp_host_write_pin_change_parks_before_actual_effect_and_resumes_once(tmp_path):
    from test_engines_acp import _engine
    async def run():
        service=BrowserService(tmp_path/'browser');authority(service);approvals=[]
        class Reviewer:
            version='qualified-v1';expected_model='snapshot-A';calls=0
            async def evaluate(self,*_):
                self.calls+=1;return ReviewVerdict('ALLOW','aligned','Typed edit',self.version,time()+15)
        reviewer=Reviewer();service.review.reviewer=reviewer;original=service.review.authorize
        async def swap(*args,**kwargs):
            row=await original(*args,**kwargs)
            if row.get('decision_source')=='model':reviewer.expected_model='snapshot-B'
            return row
        service.review.authorize=swap;engine=_engine([],approvals);engine.browser_service=service;effect=None
        try:
            binding=await engine.create_session(EffectiveRunConfiguration(cwd=str(tmp_path),tools={'managed_task_id':'managed-task'}));client=engine._sessions[binding.binding_id]['client']
            effect=asyncio.create_task(client.write_text_file(binding.native_session_id,'once.py','VALUE = 1\n'))
            async with asyncio.timeout(10):
                while not approvals:await asyncio.sleep(.02)
            assert not (tmp_path/'once.py').exists() and approvals[0][1]=='browser.review'
            await engine.respond_approval(binding,approvals[0][0],'approve');await effect
            assert (tmp_path/'once.py').read_text()=='VALUE = 1\n' and reviewer.calls==1 and len(approvals)==1
            assert len([row for row in service.records.list('review') if row['status']=='completed'])==1
        finally:
            if effect and not effect.done():effect.cancel();await asyncio.gather(effect,return_exceptions=True)
            await engine.shutdown()
    asyncio.run(run())
