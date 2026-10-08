"""Actual managed-page challenge gates; no provider query or CAPTCHA solving."""
import asyncio
import os
from time import time

import pytest

from termx.auto_review import ActionBlocked, DecisionBroker, ReviewVerdict
from termx.browser.service import BrowserService
from termx.browser.storage import Records
from test_auto_review import envelope


def test_typed_challenge_cannot_use_model_rule_or_existing_exact_human_permit(tmp_path):
    async def run():
        calls = []
        class Reviewer:
            version = 'fixture'
            async def evaluate(self, action, context):
                calls.append(action.action_id)
                return ReviewVerdict('ALLOW', 'aligned', '', self.version, time()+30)
        broker = DecisionBroker(Records(tmp_path), Reviewer())
        action = envelope(intended_effect='challenge')
        with pytest.raises(ValueError):broker.rule(action, 'ALLOW', expires_at=time()+30)
        # Even an injected pre-existing exact approval cannot authorize a
        # recognized challenge: the host boundary precedes permit reuse.
        broker._record(action, 'ALLOW', 'exact human decision', 'approved_once')
        with pytest.raises(ActionBlocked, match='human takeover/private login'):
            await broker.authorize(action, validate=lambda: True, existing_consent=lambda: True)
        assert calls == [] and broker.records.get('review', action.action_id)['status'] == 'blocked'
    asyncio.run(run())


@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), reason='managed Chromium artifact required')
def test_real_page_manual_challenge_controls_and_late_provider_marker(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site = await website()
        service = BrowserService(tmp_path, development_origins=[site]); calls=[]
        class Reviewer:
            version = 'fixture'
            async def evaluate(self, action, context):
                calls.append(action.action_id)
                return ReviewVerdict('ALLOW','aligned','',self.version,time()+30)
        service.review.reviewer=Reviewer()
        try:
            profile=service.create_profile('owner','project','Manual challenge fixture')
            tab=await service.create_tab('owner','sid',profile['id'],site)
            page=service._pages[tab['id']]
            # Provider markers are observed in DOM only. Abort their fixture
            # URLs locally so this test never contacts a challenge service.
            await page.route('https://www.google.com/recaptcha/**',lambda route:route.abort())
            await page.evaluate("()=>document.body.innerHTML='<label><input id=ordinary type=checkbox>Remember display setting</label>'")
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','click','type'],policy_version=1)
            async def act(identifier, action, args):
                current=service.get(tab['id'],'owner')
                return await service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],policy_version=1,action_id=identifier,action=action,args=args,document_revision=current['document_revision'],lease_revision=current['lease_revision'])
            await act('ordinary-checkbox','click',{'selector':'#ordinary'})
            assert await page.locator('#ordinary').is_checked() and calls==['ordinary-checkbox']
            cases=[
                ('<label><input id=target type=checkbox>I am human</label>','click',{'selector':'#target'}),
                ('<label for=target>Authenticator verification code</label><input id=target>','type',{'selector':'#target','text':'FIXTURE_CODE'}),
                ('<form aria-label="Two-factor authentication"><button id=target>Continue</button></form>','click',{'selector':'#target'}),
                ('<form><input autocomplete="one-time-code"><button id=target>Continue</button></form>','click',{'selector':'#target'}),
                ('<div data-sitekey="fixture-public-key"><input id=target type=checkbox></div>','click',{'selector':'#target'}),
                ('<iframe title="reCAPTCHA challenge" src="about:blank"></iframe><input id=target type=checkbox>','click',{'selector':'#target'}),
                ('<iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe><input id=target type=checkbox>','click',{'selector':'#target'}),
            ]
            for index,(html,action,args) in enumerate(cases):
                await page.evaluate('html=>document.body.innerHTML=html',html)
                with pytest.raises(ActionBlocked,match='human takeover/private login'):
                    await act('manual-'+str(index),action,args)
                assert not await page.locator('#target').evaluate("e=>e.type==='checkbox'?e.checked:e.value")
            assert calls==['ordinary-checkbox']
            # A provider frame that appears while an otherwise ordinary exact
            # action is queued must be checked again immediately before input.
            await page.evaluate("()=>document.body.innerHTML='<input id=target type=checkbox>'")
            await page.wait_for_load_state('load')
            # A denied foreign iframe request can independently revoke the
            # first lease. Renew explicitly after removing that test frame.
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','click','type'],policy_version=1)
            current=service.get(tab['id'],'owner');digest=await service._document_hash(page,'click',{'selector':'#target'})
            await page.evaluate("()=>{const f=document.createElement('iframe');f.src='about:blank';f.title='hCAPTCHA';document.body.append(f)}")
            with pytest.raises(PermissionError,match='human takeover/private login'):
                await service._perform(page,current,'click',{'selector':'#target'},human=False,expected_hash=digest)
            assert not await page.locator('#target').is_checked()
            # Takeover remains a real human path, never a recorded automation
            # recipe. Classify before its onclick removes the solved control.
            service.takeover(tab['id'],'owner',private=False)
            current=service.get(tab['id'],'owner');current['recording']=True;service.records.put('tab',tab['id'],current)
            await page.evaluate("()=>{document.body.innerHTML='<label><input id=target type=checkbox>I am human</label>';document.querySelector('#target').onclick=e=>e.target.closest('label').remove()}")
            await service.human_action(tab['id'],'owner','click',{'selector':'#target'})
            assert await page.locator('#target').count()==0 and not service.records.list('recording-step')
            assert 'FIXTURE_CODE' not in service.records.path.read_text(errors='ignore')
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())
