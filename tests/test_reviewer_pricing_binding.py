"""No paid queries: actual Responses parsing, frozen pricing and live credential revocation."""
import asyncio
import json
from datetime import date, timedelta
from time import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from termx.auto_review import DecisionBroker, ReviewRequired, ReviewVerdict, ResponsesReviewer
from termx.browser.evaluation import CASES, evaluate_reviewer
from termx.browser.pricing import PricingSnapshot, estimate_cost
from termx.browser.storage import Records
from test_auto_review import envelope

PRICING={'currency':'USD','input_per_million':'2','cached_input_per_million':'0.5','output_per_million':'8',
         'source':'Explicit test fixture, not a market price','effective_date':'2026-01-01','version':'fixture-price-v1'}
USAGE={'requests':1,'input_tokens':100,'cached_input_tokens':40,'output_tokens':10,'missing_usage_requests':0,'missing_cached_usage_requests':0}

def test_explicit_pricing_computes_precise_estimate_and_discloses_missing_information():
    cost=estimate_cost(USAGE,PricingSnapshot(**PRICING),provider_id='fixture',model='small')
    assert cost['reported'] and cost['amount']=='0.00022' and not cost['billed_charge']
    assert cost['pricing']==PRICING and cost['provider_id']=='fixture' and cost['model']=='small'
    missing=estimate_cost({**USAGE,'missing_cached_usage_requests':1},PricingSnapshot(**PRICING),provider_id='fixture',model='small')
    assert not missing['reported'] and missing['bounds']=={'minimum':'0.00013','maximum':'0.00028'}
    assert not estimate_cost({**USAGE,'missing_usage_requests':1},PricingSnapshot(**PRICING),provider_id='fixture',model='small')['reported']
    assert not estimate_cost(USAGE,None,provider_id='fixture',model='small')['reported']
    equal=PricingSnapshot(**{**PRICING,'cached_input_per_million':'2'})
    assert estimate_cost({**USAGE,'missing_cached_usage_requests':1},equal,provider_id='fixture',model='small')['amount']=='0.00028'
    future=PricingSnapshot(**{**PRICING,'effective_date':date.today()+timedelta(days=1)})
    assert not estimate_cost(USAGE,future,provider_id='fixture',model='small')['reported']

@pytest.mark.parametrize('field,value',[('input_per_million','-1'),('cached_input_per_million','NaN'),('output_per_million','Infinity'),('input_per_million','1000001'),('output_per_million','0.000000001'),('currency','usd'),('source',''),('source','   '),('version',''),('version','   '),('effective_date','yesterday')])
def test_snapshot_rejects_unbounded_or_ambiguous_admin_inputs(field,value):
    with pytest.raises(ValidationError):PricingSnapshot(**{**PRICING,field:value})

@pytest.mark.parametrize('usage',[{'input_tokens':True,'output_tokens':10},{'input_tokens':1_000_000_001,'output_tokens':0},{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':101}},{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':True}}])
def test_invalid_usage_cannot_claim_a_complete_cost_but_does_not_alter_verdict(usage):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'usage':usage,'output':[{'type':'message','content':[{'type':'output_text','text':'{"decision":"ALLOW","reason_code":"aligned"}'}]}]}))) as client:
            reviewer=ResponsesReviewer(provider_id='fixture',base_url='https://fixture.example',model='small',api_key='fixture',version='v1',client=client)
            assert (await reviewer.evaluate(envelope(intended_effect='edit'),{})).decision=='ALLOW'
            assert not estimate_cost(reviewer.usage,PricingSnapshot(**PRICING),provider_id='fixture',model='small')['reported']
    asyncio.run(run())

def test_qualification_usage_delta_counts_cached_tokens_without_prior_requests():
    async def run():
        expected={case:decision for case,_,_,decision in CASES}
        def response(request):
            action=json.loads(json.loads(request.content)['input'])['action']
            return httpx.Response(200,json={'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':40}},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'decision':expected.get(action['action_id'],'ALLOW'),'reason_code':'aligned'})}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            reviewer=ResponsesReviewer(provider_id='fixture',base_url='https://fixture.example',model='small',api_key='fixture',version='v1',client=client)
            await reviewer.evaluate(envelope(intended_effect='edit'),{})
            report=await evaluate_reviewer(reviewer,PricingSnapshot(**PRICING))
            assert report['qualified'] and report['qualification_evidence_complete']
            assert report['usage']['requests']==len(CASES) and report['usage']['cached_input_tokens']==40*len(CASES)
            assert report['cost']['amount']=='0.00462'
    asyncio.run(run())

def fixture(tmp_path,monkeypatch):
    from termx.app import AppState,create_app
    from termx.agent.secrets import CredentialStore
    from termx.identity import AuthenticationService
    from termx.browser import router
    monkeypatch.setenv('TERMX_CONFIG_DIR',str(tmp_path/'config'))
    identity=AuthenticationService(tmp_path/'identity.sqlite3')
    identity.setup_owner('owner','fixture-review-password-123')
    token=asyncio.run(identity.login('local-password',{'username':'owner','password':'fixture-review-password-123'},peer='127.0.0.1')).access_token
    credentials=CredentialStore({'fixture':'fixture-private-account-A'})
    state=AppState(identity=identity,credentials=credentials)
    state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Explicit fixture',base_url='https://fixture.example',model='small',capabilities=['functions'])
    expected={case:decision for case,_,_,decision in CASES}
    class FixtureReviewer(ResponsesReviewer):
        def __init__(self,**kwargs):
            def response(request):
                action=json.loads(json.loads(request.content)['input'])['action']
                return httpx.Response(200,json={'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':40}},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'decision':expected.get(action['action_id'],'ALLOW'),'reason_code':'aligned'})}]}]})
            super().__init__(**kwargs,client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    monkeypatch.setattr(router,'ResponsesReviewer',FixtureReviewer)
    return state,TestClient(create_app(state),base_url='https://localhost'),{'Authorization':'Bearer '+token}

def test_frozen_reports_export_without_secrets_and_account_switch_invalidates_activation_and_cached_permit(tmp_path,monkeypatch):
    state,client,headers=fixture(tmp_path,monkeypatch)
    body={'provider_id':'fixture','model':'small','version':'fixture-v1','pricing':PRICING}
    result=client.post('/api/browser/reviewer/evaluate',headers=headers,json=body)
    assert result.status_code==200,result.text
    report=result.json();assert report['qualified'] and report['cost']['reported']
    assert client.post('/api/browser/reviewer',headers=headers,json={key:body[key] for key in ('provider_id','model','version')}).status_code==200
    action=envelope(intended_effect='edit')
    permit=asyncio.run(state.browser.review.authorize(action,validate=lambda:True))
    assert permit['decision_source']=='model'
    state.agent.save_provider(provider_id='fixture',kind='openai-compatible',name='Reused account ID',base_url='https://fixture.example',model='small',capabilities=['functions'],api_key='fixture-private-account-B')
    status=client.get('/api/browser/reviewer',headers=headers)
    assert not status.json()['active'] and not status.json()['evaluations'][0]['activation_eligible']
    assert client.post('/api/browser/reviewer',headers=headers,json={key:body[key] for key in ('provider_id','model','version')}).status_code==400
    calls=[]
    async def effect():calls.append(True)
    with pytest.raises(ValueError):asyncio.run(state.browser.review.execute(action,permit['permit'],validate=lambda:True,operation=effect))
    with pytest.raises(ValueError):asyncio.run(state.browser.review.consume_external(action,permit['permit'],validate=lambda:True))
    with pytest.raises(ReviewRequired):asyncio.run(state.browser.review.authorize(action,validate=lambda:True))
    assert not calls
    pending=state.browser.records.get('review',action.action_id)
    assert pending['status']=='needs_user' and 'permit' not in pending
    exported=client.get('/api/browser/reviewer/evaluations/'+report['id']+'/export',headers=headers)
    assert exported.json()['cost']==report['cost'] and not exported.json()['activation_eligible']
    private=state.browser.records.get('reviewer-private-binding','fixture')
    for value in ('fixture-private-account-A','fixture-private-account-B',private['fingerprint'],private['salt']):
        assert value not in status.text and value not in exported.text
    fresh=client.post('/api/browser/reviewer/evaluate',headers=headers,json={**body,'pricing':{**PRICING,'input_per_million':'3','version':'fixture-price-v2'}}).json()
    assert fresh['id']!=report['id'] and fresh['account_revision']!=report['account_revision']
    assert client.get('/api/browser/reviewer/evaluations/'+report['id']+'/export',headers=headers).json()['cost']==report['cost']
    state.browser.review.decide(action.action_id,principal_id=action.principal_id,approve=True)
    exact=asyncio.run(state.browser.review.authorize(action,validate=lambda:True))
    asyncio.run(state.browser.review.execute(action,exact['permit'],validate=lambda:True,operation=effect))
    assert calls==[True]

def test_account_change_during_verdict_fails_closed_but_exact_human_approval_remains_separate(tmp_path):
    valid=True
    class Reviewer:
        version='fixture-v1';account_revision='account-A'
        async def evaluate(self,action,context):
            nonlocal valid
            valid=False
            return ReviewVerdict('ALLOW','aligned','test',self.version,time()+15)
    broker=DecisionBroker(Records(tmp_path),Reviewer());broker.reviewer_valid=lambda:valid
    action=envelope(intended_effect='edit')
    with pytest.raises(ReviewRequired) as pending:asyncio.run(broker.authorize(action,validate=lambda:True))
    assert pending.value.record['decision']=='NEEDS_USER' and pending.value.record['reason']=='reviewer_unavailable'
    broker.decide(action.action_id,principal_id=action.principal_id,approve=True)
    permit=asyncio.run(broker.authorize(action,validate=lambda:True))
    calls=[]
    async def effect():calls.append(True)
    asyncio.run(broker.execute(action,permit['permit'],validate=lambda:True,operation=effect))
    assert calls==[True]


def test_actual_configured_account_rotation_after_response_rejects_verdict_even_when_safety_qualified_without_price(tmp_path,monkeypatch):
    state,client,headers=fixture(tmp_path,monkeypatch)
    body={'provider_id':'fixture','model':'small','version':'fixture-v1'}
    report=client.post('/api/browser/reviewer/evaluate',headers=headers,json=body).json()
    assert report['qualified'] and not report['qualification_evidence_complete'] and not report['cost']['reported']
    assert client.post('/api/browser/reviewer',headers=headers,json=body).status_code==200
    reviewer=state.browser.review.reviewer
    original=reviewer.evaluate
    async def rotate_after_response(action,context):
        verdict=await original(action,context)
        state.credentials.set('fixture','fixture-private-account-C')
        return verdict
    reviewer.evaluate=rotate_after_response
    with pytest.raises(ReviewRequired) as pending:
        asyncio.run(state.browser.review.authorize(envelope(action_id='rotated-after-verdict',intended_effect='edit'),validate=lambda:True))
    assert pending.value.record['decision']=='NEEDS_USER' and pending.value.record['reason']=='reviewer_unavailable'
    assert not client.get('/api/browser/reviewer',headers=headers).json()['active']


def test_model_cannot_be_confused_with_host_rules_by_using_the_same_version_name(tmp_path):
    valid=True
    class Reviewer:
        version='host-policy-v1';account_revision='account-A'
        async def evaluate(self,action,context):return ReviewVerdict('ALLOW','aligned','test',self.version,time()+15)
    broker=DecisionBroker(Records(tmp_path),Reviewer());broker.reviewer_valid=lambda:valid
    action=envelope(intended_effect='edit')
    permit=asyncio.run(broker.authorize(action,validate=lambda:True))
    assert permit['decision_source']=='model'
    valid=False
    calls=[]
    async def effect():calls.append(True)
    with pytest.raises(ValueError):asyncio.run(broker.execute(action,permit['permit'],validate=lambda:True,operation=effect))
    assert not calls and broker.records.get('review',action.action_id)['status']=='needs_user'
