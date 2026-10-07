from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from termx.agent.store import AgentStore
from termx.agent.tree_budget import TreeBudget

@pytest.fixture
def tree(tmp_path):
 store=AgentStore(tmp_path/'agents.db',tmp_path/'artifacts');clock=[100.0]
 limits={'max_steps':3,'max_seconds':10,'shell_timeout_s':10,'max_parallel_subagents':3,'max_subagents_total':8}
 parent=store.create_task(prompt='parent',cwd=str(tmp_path),provider_id='fixture',model='fixture',limits=limits)
 children=[store.create_task(prompt=f'child{i}',cwd=str(tmp_path),provider_id='fixture',model='fixture',limits={**limits,'max_steps':24},parent_id=parent['id']) for i in range(2)]
 budget=TreeBudget(store,clock=lambda:clock[0]);budget.bind(parent['id'])
 for child in children:budget.bind(child['id'])
 yield SimpleNamespace(store=store,budget=budget,clock=clock,parent=parent,children=children)
 store.close()

def test_concurrent_parent_children_cannot_multiply_pooled_steps(tree):
 ids=[tree.parent['id'],*[child['id'] for child in tree.children]]
 with ThreadPoolExecutor(max_workers=9) as executor:
  results=list(executor.map(lambda item:tree.budget.reserve(item[0],[f'call-{item[1]}']),[(ids[i%3],i) for i in range(30)]))
 assert results.count(None)==3
 for task in ids:assert tree.budget.snapshot(task)['used_steps']==3
 assert tree.budget.snapshot(ids[0])['reason']=='shared_max_steps'

def test_exact_parked_batch_reservation_is_idempotent_and_all_or_nothing(tree):
 parent=tree.parent['id'];child=tree.children[0]['id']
 assert tree.budget.reserve(parent,['call-0','call-1']) is None
 assert tree.budget.reserve(parent,['call-0','call-1']) is None
 assert tree.budget.reserve(child,['call-0','call-1'])=='shared_max_steps'
 assert tree.budget.snapshot(parent)['used_steps']==2
 assert tree.budget.reserve(child,['call-0']) is None
 assert tree.budget.reserve(parent,['call-0','call-1']) is None
 assert tree.budget.reserve(parent,['call-2'])=='shared_max_steps'

def test_time_is_sum_of_active_parent_child_workers_and_pauses_stop_charging(tree):
 parent=tree.parent['id'];child=tree.children[0]['id']
 tree.budget.start(parent);tree.budget.start(child);tree.clock[0]+=3
 assert tree.budget.snapshot(parent)['used_execution_seconds']==6
 tree.budget.stop(parent);tree.budget.stop(child);tree.clock[0]+=100
 assert tree.budget.snapshot(child)['used_execution_seconds']==6
 tree.budget.start(child);tree.clock[0]+=4
 assert tree.budget.reserve(child,['later'])=='shared_max_seconds'
 assert tree.budget.snapshot(parent)['used_steps']==0

def test_restart_keeps_steps_and_uncertain_live_time_requires_explicit_renewal(tree):
 parent=tree.parent['id'];tree.budget.reserve(parent,['completed']);tree.budget.start(tree.children[0]['id'])
 recovered=TreeBudget(tree.store,clock=lambda:tree.clock[0]);snapshot=recovered.snapshot(parent)
 assert snapshot['used_steps']==1 and snapshot['reason']=='shared_max_seconds'
 with pytest.raises(ValueError,match='changed'):recovered.renew(parent,{'max_steps':4,'max_seconds':10},version=999)
 renewed=recovered.renew(parent,{'max_steps':4,'max_seconds':10},version=snapshot['version'])
 assert renewed['used_steps']==1 and renewed['used_execution_seconds']==0 and renewed['version']==2
 assert recovered.reserve(tree.children[0]['id'],['new']) is None
 assert recovered.snapshot(parent)['used_steps']==2


def test_actual_concurrent_child_calls_share_parent_pool_and_resume_exact_batch(tmp_path):
 import asyncio
 from collections import defaultdict
 from termx.agent.providers import ProviderCall,ProviderTurn
 from test_agent import build_manager,wait_for_status
 class Adapter:
  def __init__(self):self.count=defaultdict(int);self.children=0;self.ready=asyncio.Event();self.parent=asyncio.Event()
  async def turn(self,**kwargs):
   prompt=kwargs['prompt'];self.count[prompt]+=1
   if prompt=='Parent':
    await self.parent.wait();return ProviderTurn('parent','Parent finished',[],{},[])
   if self.count[prompt]==1:
    self.children+=1
    if self.children==2:self.ready.set()
    await self.ready.wait()
    return ProviderTurn(prompt,'',[ProviderCall('function',prompt+'-'+str(i),'read_file',{'path':'read.txt'}) for i in range(2)],{},[])
   return ProviderTurn(prompt+'-done','Child finished',[],{},[])
 async def run():
  (tmp_path/'read.txt').write_text('Scoped fixture')
  adapter=Adapter();manager,store=build_manager(tmp_path,adapter)
  try:
   parent=await manager.create_task(prompt='Parent',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':3,'max_seconds':10})
   children=[await manager.create_task(prompt=name,cwd=str(tmp_path),provider_id='fake',mode='ask',parent_id=parent['id'],limits={'max_steps':24,'max_seconds':10}) for name in ('Child A','Child B')]
   for _ in range(500):
    records=[store.get_task(child['id'],include_events=True) for child in children]
    if sorted(task['status'] for task in records)==['awaiting_approval','completed']:break
    await asyncio.sleep(.01)
   assert sorted(task['status'] for task in records)==['awaiting_approval','completed'],records
   assert manager.tree_budget.snapshot(parent['id'])['used_steps']==2
   assert sum(len([e for e in store.events(child['id']) if e['type']=='tool.finished']) for child in children)==2
   paused=next(task for task in records if task['status']=='awaiting_approval');approval=next(a for a in paused['approvals'] if a['kind']=='budget' and a['status']=='pending')
   assert approval['payload']['tree_budget']['root_task_id']==parent['id']
   await manager.resolve_approval(paused['id'],approval['id'],'approved',limits={'max_steps':5,'max_seconds':10})
   await wait_for_status(store,paused['id'],'completed')
   assert sum(len([e for e in store.events(child['id']) if e['type']=='tool.finished']) for child in children)==4
   assert manager.tree_budget.snapshot(parent['id'])['used_steps']==4
   adapter.parent.set();await wait_for_status(store,parent['id'],'completed')
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_actual_parallel_provider_work_stops_at_summed_seconds_before_effect(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderTurn
 from test_agent import build_manager
 class Adapter:
  async def turn(self,**kwargs):
   await asyncio.sleep(20);return ProviderTurn('late','Should not finish',[],{},[])
 async def run():
  manager,store=build_manager(tmp_path,Adapter())
  try:
   parent=await manager.create_task(prompt='Parent',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':10,'max_seconds':1})
   children=[await manager.create_task(prompt='Child '+str(i),cwd=str(tmp_path),provider_id='fake',mode='ask',parent_id=parent['id'],limits={'max_steps':24,'max_seconds':10}) for i in range(2)]
   for _ in range(500):
    records=[store.get_task(task['id'],include_events=True) for task in [parent,*children]]
    if all(task['status']=='awaiting_approval' for task in records):break
    await asyncio.sleep(.01)
   assert all(task['status']=='awaiting_approval' for task in records),records
   assert manager.tree_budget.snapshot(parent['id'])['used_execution_seconds']>=1
   assert manager.tree_budget.snapshot(parent['id'])['active_workers']==0
   assert all(next(a for a in task['approvals'] if a['status']=='pending')['payload']['reason']=='shared_max_seconds' for task in records)
   assert all(not any(e['type']=='tool.started' for e in store.events(task['id'])) for task in records)
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_independent_store_connections_reserve_one_pool_atomically(tree, tmp_path):
 other = AgentStore(tmp_path/'agents.db', tmp_path/'artifacts')
 try:
  second = TreeBudget(other, clock=lambda:tree.clock[0])
  ids = [tree.parent['id'], *[child['id'] for child in tree.children]]
  with ThreadPoolExecutor(max_workers=8) as executor:
   results = list(executor.map(lambda i:(tree.budget if i % 2 else second).reserve(ids[i % 3], [f'cross-connection-{i}']), range(24)))
  assert results.count(None) == 3
  assert second.snapshot(ids[0])['used_steps'] == 3
 finally:
  other.close()


def test_invalid_renewal_and_ancestry_leave_no_open_transaction(tree):
 parent = tree.parent['id']
 tree.budget.reserve(parent, ['reserved'])
 with pytest.raises(ValueError, match='ceiling'):
  tree.budget.renew(parent, {'max_steps':1, 'max_seconds':10}, version=1)
 assert not tree.store._db.in_transaction
 with pytest.raises(ValueError, match='integer'):
  tree.budget.renew(parent, {'max_steps':4, 'max_seconds':True}, version=1)
 assert tree.budget.snapshot(parent)['version'] == 1
 # Malformed durable ancestry fails before any partial member is enrolled.
 child = tree.store.create_task(prompt='cycle', cwd=parent, provider_id='fixture', model='fixture', limits=tree.parent['limits'])
 tree.store._db.execute('UPDATE tasks SET parent_id=? WHERE id=?', (child['id'], child['id']))
 tree.store._db.commit()
 with pytest.raises(ValueError, match='cycle'):
  tree.budget.bind(child['id'])
 assert not tree.store._db.in_transaction


def test_actual_cancelled_provider_releases_lease_without_resetting_tree(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderTurn
 from test_agent import build_manager, wait_for_status
 class Adapter:
  def __init__(self):self.started=asyncio.Event();self.cancelled=asyncio.Event()
  async def turn(self, **kwargs):
   self.started.set()
   try:await asyncio.sleep(30)
   finally:self.cancelled.set()
   return ProviderTurn('unexpected', 'Should not complete', [], {}, [])
 async def run():
  adapter=Adapter();manager,store=build_manager(tmp_path,adapter)
  try:
   task=await manager.create_task(prompt='Cancel pending provider',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':10,'max_seconds':10})
   await asyncio.wait_for(adapter.started.wait(), 2)
   manager.tree_budget.reserve(task['id'], ['already-reserved'])
   await asyncio.sleep(.03)
   manager.cancel(task['id'])
   await wait_for_status(store,task['id'],'cancelled')
   await asyncio.wait_for(adapter.cancelled.wait(), 2)
   for _ in range(100):
    before=manager.tree_budget.snapshot(task['id'])
    if before['active_workers']==0:break
    await asyncio.sleep(.01)
   assert before['active_workers']==0 and before['used_steps']==1
   await asyncio.sleep(.03)
   after=manager.tree_budget.snapshot(task['id'])
   assert after['used_execution_seconds']==before['used_execution_seconds']
   assert not any(e['type']=='tool.started' for e in store.events(task['id']))
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_actual_sibling_renewals_reuse_one_explicit_tree_time_grant(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderTurn
 from test_agent import build_manager, wait_for_status
 class Adapter:
  def __init__(self):self.release=asyncio.Event()
  async def turn(self, **kwargs):
   await self.release.wait()
   return ProviderTurn(kwargs['prompt'], 'Finished after explicit grant', [], {}, [])
 async def run():
  adapter=Adapter();manager,store=build_manager(tmp_path,adapter)
  try:
   parent=await manager.create_task(prompt='Parent',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':10,'max_seconds':1})
   child=await manager.create_task(prompt='Child',cwd=str(tmp_path),provider_id='fake',mode='ask',parent_id=parent['id'],limits={'max_steps':20,'max_seconds':10})
   await wait_for_status(store,parent['id'],'awaiting_approval')
   await wait_for_status(store,child['id'],'awaiting_approval')
   def pending(task):return next(a for a in store.approvals(task['id']) if a['kind']=='budget' and a['status']=='pending')
   parent_approval, child_approval=pending(parent), pending(child)
   await manager.resolve_approval(parent['id'],parent_approval['id'],'approved',limits={'max_steps':20,'max_seconds':5})
   await asyncio.sleep(.04)
   before=manager.tree_budget.snapshot(parent['id'])
   assert before['version']==2 and before['used_execution_seconds']>0
   await manager.resolve_approval(child['id'],child_approval['id'],'approved',limits={'max_steps':999,'max_seconds':999})
   after=manager.tree_budget.snapshot(child['id'])
   assert after['version']==2 and after['max_execution_seconds']==5 and after['max_steps']==20
   assert after['used_execution_seconds']>=before['used_execution_seconds']
   with pytest.raises(ValueError):
    await manager.resolve_approval(child['id'],child_approval['id'],'approved')
   assert manager.tree_budget.snapshot(parent['id'])['version']==2
   adapter.release.set()
   await wait_for_status(store,parent['id'],'completed')
   await wait_for_status(store,child['id'],'completed')
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_actual_restart_keeps_parked_batch_and_requires_the_original_budget_decision(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderCall, ProviderTurn
 from test_agent import build_manager, wait_for_status
 class Adapter:
  def __init__(self, resumed=False):self.turns=0;self.resumed=resumed
  async def turn(self, **kwargs):
   self.turns+=1
   if self.resumed:return ProviderTurn('finished','Saved work finished',[],{},[])
   return ProviderTurn('batch','',[ProviderCall('function','read-'+str(i),'read_file',{'path':'read.txt'}) for i in range(2)],{},[])
 async def run():
  (tmp_path/'read.txt').write_text('Restart fixture')
  first=Adapter();manager,store=build_manager(tmp_path,first)
  task=await manager.create_task(prompt='Persist exact batch',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':1,'max_seconds':10})
  await wait_for_status(store,task['id'],'awaiting_approval')
  approval=next(a for a in store.approvals(task['id']) if a['kind']=='budget' and a['status']=='pending')
  await manager.close();store.close()
  resumed=Adapter(resumed=True);manager,store=build_manager(tmp_path,resumed)
  try:
   await asyncio.sleep(.03)
   assert resumed.turns==0 and store.get_task(task['id'])['status']=='awaiting_approval'
   assert store.get_approval(approval['id'])['status']=='pending'
   assert manager.tree_budget.snapshot(task['id'])['used_steps']==0
   await manager.resolve_approval(task['id'],approval['id'],'approved',limits={'max_steps':3,'max_seconds':10})
   await wait_for_status(store,task['id'],'completed')
   finished=[e['payload']['call_id'] for e in store.events(task['id']) if e['type']=='tool.finished']
   assert finished==['read-0','read-1'] and resumed.turns==1
   assert manager.tree_budget.snapshot(task['id'])['used_steps']==2
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_actual_parallel_child_planning_consumes_pool_and_renewal_still_requires_plan_consent(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderTurn
 from test_agent import build_manager, wait_for_status
 class Adapter:
  def __init__(self):self.plans=0;self.plans_started=asyncio.Event();self.release=asyncio.Event();self.cancelled_plans=0;self.child_turns=0
  async def plan(self, prompt, cwd, manifest):
   self.plans+=1
   if self.plans==2:self.plans_started.set()
   try:await self.release.wait()
   except asyncio.CancelledError:self.cancelled_plans+=1;raise
   return {'summary':prompt,'steps':['Inspect'],'tools':['read_file'],'risks':[]}, 'plan-'+prompt
  async def turn(self, **kwargs):
   if kwargs['prompt']=='Parent':await asyncio.sleep(30)
   else:self.child_turns+=1
   return ProviderTurn('finished','Finished after explicit plan consent',[],{},[])
 async def run():
  adapter=Adapter();manager,store=build_manager(tmp_path,adapter);clock=[100.0];manager.tree_budget.clock=lambda:clock[0]
  try:
   parent=await manager.create_task(prompt='Parent',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':20,'max_seconds':1})
   creators=[asyncio.create_task(manager.create_task(prompt='Child '+str(i),cwd=str(tmp_path),provider_id='fake',parent_id=parent['id'],limits={'max_steps':20,'max_seconds':10})) for i in range(2)]
   await asyncio.wait_for(adapter.plans_started.wait(),5)
   clock[0]+=2
   children=await asyncio.wait_for(asyncio.gather(*creators),5)
   assert adapter.cancelled_plans==2 and adapter.child_turns==0
   assert all(task['status']=='awaiting_approval' and task['runtime']['tree_phase']=='planning' for task in children)
   assert manager.tree_budget.snapshot(parent['id'])['used_execution_seconds']>=6
   chosen=children[0];budget=next(a for a in store.approvals(chosen['id']) if a['kind']=='budget' and a['status']=='pending')
   adapter.release.set()
   await manager.resolve_approval(chosen['id'],budget['id'],'approved',limits={'max_steps':20,'max_seconds':10})
   for _ in range(100):
    plans=[a for a in store.approvals(chosen['id']) if a['kind']=='plan' and a['status']=='pending']
    if plans:break
    await asyncio.sleep(.01)
   assert plans and adapter.child_turns==0
   assert store.get_task(chosen['id'])['status']=='awaiting_approval'
   await manager.resolve_approval(chosen['id'],plans[0]['id'],'approved')
   await wait_for_status(store,chosen['id'],'completed')
   assert adapter.child_turns==1
  finally:await manager.close();store.close()
 asyncio.run(run())


def test_approval_and_grant_rollback_together_when_durable_decision_cannot_commit(tree):
 import sqlite3
 parent=tree.parent['id']
 approval=tree.store.create_approval(parent,'budget',{'tree_budget':tree.budget.snapshot(parent),'suggested_limits':{'max_steps':5}})
 tree.store._db.execute("CREATE TRIGGER reject_grant BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT,'fixture decision write failed'); END")
 tree.store._db.commit()
 with pytest.raises(sqlite3.IntegrityError,match='decision write failed'):
  tree.budget.approve(parent,approval['id'],{'max_steps':5,'max_seconds':20},version=1)
 assert tree.budget.snapshot(parent)['version']==1
 assert tree.budget.snapshot(parent)['max_steps']==3
 assert tree.store.get_approval(approval['id'])['status']=='pending'
 assert not (tree.store.get_task(parent).get('runtime') or {}).get('tree_budget_resume')
 assert not tree.store._db.in_transaction


def test_actual_restart_after_committed_grant_requires_continue_without_granting_twice(tmp_path):
 import asyncio
 from termx.agent.providers import ProviderCall,ProviderTurn
 from test_agent import build_manager,wait_for_status
 class Adapter:
  def __init__(self, resumed=False):self.resumed=resumed;self.turns=0
  async def turn(self,**kwargs):
   self.turns+=1
   if self.resumed:return ProviderTurn('done','Finished saved batch',[],{},[])
   return ProviderTurn('batch','',[ProviderCall('function','call-'+str(i),'read_file',{'path':'read.txt'}) for i in range(2)],{},[])
 async def run():
  (tmp_path/'read.txt').write_text('Grant receipt fixture')
  manager,store=build_manager(tmp_path,Adapter())
  task=await manager.create_task(prompt='Persist approved grant',cwd=str(tmp_path),provider_id='fake',mode='ask',limits={'max_steps':1,'max_seconds':10})
  await wait_for_status(store,task['id'],'awaiting_approval')
  approval=next(a for a in store.approvals(task['id']) if a['kind']=='budget' and a['status']=='pending')
  # Commit the decision, but deliberately do not launch its worker.
  manager.tree_budget.approve(task['id'],approval['id'],{'max_steps':3,'max_seconds':10},version=1)
  await manager.close();store.close()
  adapter=Adapter(resumed=True);manager,store=build_manager(tmp_path,adapter)
  try:
   assert adapter.turns==0 and store.get_approval(approval['id'])['status']=='approved'
   resumed=next(a for a in store.approvals(task['id']) if a['kind']=='budget' and a['status']=='pending')
   assert resumed['payload']['reuse_grant'] is True and resumed['payload']['tree_budget']['version']==2
   await manager.resolve_approval(task['id'],resumed['id'],'approved',limits={'max_steps':999,'max_seconds':999})
   await wait_for_status(store,task['id'],'completed')
   snapshot=manager.tree_budget.snapshot(task['id'])
   assert snapshot['version']==2 and snapshot['max_steps']==3 and snapshot['max_execution_seconds']==10
   assert snapshot['used_steps']==2
   assert len([e for e in store.events(task['id']) if e['type']=='tool.finished'])==2
  finally:await manager.close();store.close()
 asyncio.run(run())
