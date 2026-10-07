import asyncio
from dataclasses import replace
from time import time
import pytest
import httpx
from termx.auto_review import ActionEnvelope,DecisionBroker,ReviewVerdict,ReviewRequired,ActionBlocked,ResponsesReviewer,canonical_hash
from termx.browser.storage import Records
from termx.browser.evaluation import evaluate_reviewer,CASES

def envelope(**changes):
    a=ActionEnvelope('action','principal','session','project','run','browser.observe',canonical_hash({'foo':'bar'}),'https://example.test','observe','grant',1,1,1,'profile')
    return replace(a,**changes)

def test_dedup_one_use_and_changed_args(tmp_path):
    async def run():
        records=Records(tmp_path);broker=DecisionBroker(records);a=envelope()
        p1,p2=await asyncio.gather(*(broker.authorize(a,validate=lambda:True) for _ in range(2)))
        assert p1['permit']==p2['permit']
        effects=[]
        async def execute():effects.append(1);return {'ok':True}
        assert await broker.execute(a,p1['permit'],validate=lambda:True,operation=execute)=={'ok':True}
        with pytest.raises(ValueError):await broker.execute(a,p1['permit'],validate=lambda:True,operation=execute)
        with pytest.raises(ValueError):await broker.authorize(replace(a,canonical_args_hash='different'),validate=lambda:True)
        assert effects==[1]
    asyncio.run(run())

@pytest.mark.parametrize('effect',['send','publish','purchase','delete','credential','privilege','export','upload','unknown'])
def test_sensitive_host_gate_cannot_be_overridden(tmp_path,effect):
    class MaliciousReviewer:
        version='bad';calls=0
        async def evaluate(self,a,c):self.calls+=1;return ReviewVerdict('ALLOW','aligned','allowed','bad',time()+15)
    async def run():
        reviewer=MaliciousReviewer();broker=DecisionBroker(Records(tmp_path),reviewer);a=envelope(intended_effect=effect)
        with pytest.raises(ReviewRequired) as info:await broker.authorize(a,validate=lambda:True)
        assert info.value.record['decision']=='NEEDS_USER';assert reviewer.calls==0
        with pytest.raises(ValueError):broker.decide(a.action_id,principal_id='other',approve=True)
        broker.decide(a.action_id,principal_id=a.principal_id,approve=True)
        permit=await broker.authorize(a,validate=lambda:True)
        assert permit['decision']=='ALLOW'
    asyncio.run(run())

def test_hard_deny_before_exact_human_consent(tmp_path):
    async def run():
        broker=DecisionBroker(Records(tmp_path));a=envelope(intended_effect='unknown')
        with pytest.raises(ReviewRequired):await broker.authorize(a,validate=lambda:True)
        broker.decide(a.action_id,principal_id=a.principal_id,approve=True)
        with pytest.raises(ActionBlocked):await broker.authorize(a,validate=lambda:False)
    asyncio.run(run())

def test_scoped_deny_precedes_allow_and_revocation(tmp_path):
    async def run():
        broker=DecisionBroker(Records(tmp_path));a=envelope()
        allow=broker.rule(a,'ALLOW',expires_at=time()+100)
        deny=broker.rule(a,'BLOCK',expires_at=time()+100)
        with pytest.raises(ActionBlocked):await broker.authorize(a,validate=lambda:True)
        broker.revoke_rule(deny['id'])
        assert (await broker.authorize(replace(a,action_id='another'),validate=lambda:True))['decision']=='ALLOW'
        # No spill into another project.
        assert broker._scope(a)!=broker._scope(replace(a,project_id='other'))
    asyncio.run(run())

def test_review_outage_stale_and_restart_fail_closed(tmp_path):
    class Failing:
        version='v1'
        async def evaluate(self,a,c):raise RuntimeError('secret must never enter audit')
    async def run():
        records=Records(tmp_path);broker=DecisionBroker(records,Failing())
        with pytest.raises(ReviewRequired) as info:await broker.authorize(envelope(intended_effect='edit'),validate=lambda:True)
        assert info.value.record['reason']=='reviewer_unavailable'
        a=envelope(action_id='second');permit=await broker.authorize(a,validate=lambda:True)
        DecisionBroker(records)
        with pytest.raises(ValueError):await broker.execute(a,permit['permit'],validate=lambda:True,operation=lambda:None)
        assert 'secret must never' not in records.path.read_bytes().decode(errors='ignore')
    asyncio.run(run())

def test_takeover_during_model_evaluation_invalidates(tmp_path):
    valid=True
    class Reviewer:
        version='v1'
        async def evaluate(self,a,c):
            nonlocal valid
            valid=False
            return ReviewVerdict('ALLOW','aligned','ok','v1',time()+15)
    async def run():
        broker=DecisionBroker(Records(tmp_path),Reviewer())
        with pytest.raises(ActionBlocked):await broker.authorize(envelope(intended_effect='edit'),validate=lambda:valid)
    asyncio.run(run())

def test_responses_reviewer_no_tools_no_raw_args_and_strict_output():
    async def run():
        captured=[]
        def handler(request):
            import json
            body=json.loads(request.content);captured.append(body)
            return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':'{"decision":"ALLOW","reason_code":"aligned"}'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reviewer=ResponsesReviewer(provider_id='local',base_url='http://localhost:9999',model='configured-small',api_key='',version='pinned',client=client)
            verdict=await reviewer.evaluate(envelope(intended_effect='edit'),{'cookies':'secret','effect_summary':'Change display'})
            assert verdict.decision=='ALLOW'
            assert 'tools' not in captured[0] and 'secret' not in str(captured[0])
    asyncio.run(run())

def test_frozen_adversarial_eval_detects_false_allows():
    class AlwaysAllow:
        version='unsafe'
        async def evaluate(self,a,c):return ReviewVerdict('ALLOW','aligned','ok','unsafe',time()+15)
    report=asyncio.run(evaluate_reviewer(AlwaysAllow()))
    assert not report['qualified'];assert report['false_allow_count']==len(CASES)-2
