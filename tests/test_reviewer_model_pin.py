"""No live provider requests: exact response model identity and cached permits."""
import asyncio,json
import httpx,pytest
from termx.auto_review import ResponsesReviewer,DecisionBroker,ReviewRequired
from termx.browser.evaluation import CASES,evaluate_reviewer
from termx.browser.storage import Records
from test_auto_review import envelope

@pytest.mark.parametrize('models',[[None]*21,['deployment-one']*20+['deployment-two'],['']*21,['x'*201]*21])
def test_safety_fixture_pass_alone_cannot_qualify_missing_or_mixed_reported_identity(models):
    async def run():
        names=iter(models);expected={name:decision for name,_,_,decision in CASES}
        def handler(request):
            row=json.loads(json.loads(request.content)['input'])['action'];name=next(names)
            body={'usage':{'input_tokens':3,'output_tokens':2},'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'decision':expected[row['action_id']],'reason_code':'aligned'})}]}]}
            if name is not None:body['model']=name
            return httpx.Response(200,json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reviewer=ResponsesReviewer(provider_id='fixture',base_url='https://fixture.example',model='friendly-alias',api_key='',version='policy-v1',client=client)
            report=await evaluate_reviewer(reviewer)
            assert report['safety_fixture_passed'] and not report['qualified'] and not report['model_identity']['pinned'] and report['model_identity']['qualified_model'] is None
    asyncio.run(run())

def test_active_alias_shift_fails_closed_before_verdict_and_old_cached_permit_cannot_cross_pin(tmp_path):
    async def run():
        reported='qualified-deployment';calls=[]
        def handler(request):
            calls.append(json.loads(request.content)['model'])
            return httpx.Response(200,json={'model':reported,'output':[{'type':'message','content':[{'type':'output_text','text':'{"decision":"ALLOW","reason_code":"aligned"}'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reviewer=ResponsesReviewer(provider_id='fixture',base_url='https://fixture.example',model='friendly-alias',api_key='',version='policy-v1',expected_model=reported,client=client)
            broker=DecisionBroker(Records(tmp_path),reviewer);action=envelope(intended_effect='edit')
            permit=await broker.authorize(action,validate=lambda:True)
            assert permit['reviewer_model_identity']=='qualified-deployment'
            reported='changed-deployment'
            with pytest.raises(ReviewRequired) as rejected:await broker.authorize(envelope(action_id='shifted',intended_effect='edit'),validate=lambda:True)
            assert rejected.value.record['reason']=='reviewer_unavailable'
            reviewer.expected_model=reported
            effects=[]
            with pytest.raises(ReviewRequired):await broker.execute(action,permit['permit'],validate=lambda:True,operation=lambda:effects.append('effect'))
            assert broker.records.get('review',action.action_id)['status']=='needs_user'
            with pytest.raises(ReviewRequired):await broker.authorize(action,validate=lambda:True)
            assert effects==[] and calls==['qualified-deployment','qualified-deployment']
    asyncio.run(run())
