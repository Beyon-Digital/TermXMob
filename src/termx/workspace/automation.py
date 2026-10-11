"""Durable delegated schedules driven by the daemon rather than UI lifetime."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from time import time

from termx.agent.limits import resolve_limits
from termx.agent.store import ACTIVE_STATUSES
from termx.workspace.store import Conflict


from termx.workspace.schedule_spec import next_run


class AutomationService:
    def __init__(self, workspace, *, principal_lookup, clock=time):
        self.workspace = workspace
        self.store = workspace.store
        self.principal_lookup = principal_lookup
        self.clock = clock
        self._worker = None
        self._lock = asyncio.Lock()
        self._closing = False
        # A task already dispatched remains owned by AgentManager recovery.
        # An interrupted claim without task ID is ambiguous and never replayed.
        for run in self.store.list('schedule_run'):
            if run['status']=='dispatching':
                self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention',
                                  'reason':'Host restarted during dispatch; reconcile before retrying'})

    def create(self,principal,*,request_id,conversation_id,success_criteria,max_runs,limits,prompt,spec,grant_days,missed='skip',overlap='skip',runbook_id=None,command=None):
        self.workspace.record(principal,'conversation',conversation_id,scope='agent-run')
        identifier=uuid.uuid5(uuid.NAMESPACE_URL,principal.id+':schedule:'+request_id).hex
        digest=hashlib.sha256(json.dumps(dict(conversation_id=conversation_id,success_criteria=success_criteria,
            max_runs=max_runs,limits=limits,prompt=prompt,spec=spec,grant_days=grant_days,missed=missed,overlap=overlap,runbook_id=runbook_id,command=command),sort_keys=True).encode()).hexdigest()
        with self.store.lock:
            previous=self.store.get('schedule',identifier)
            if previous:
                self.workspace.record(principal,'schedule',identifier,scope='agent-control')
                if previous.get('creation_digest')!=digest: raise Conflict('Creation request changed; use a new request ID')
                return previous
            if command and runbook_id: raise ValueError('Choose a saved automation or a new command')
            runbook=self.runbook(principal,conversation_id,runbook_id) if runbook_id else None
            if command:
                conversation=self.workspace.session(principal,conversation_id,turns=False)
                self.workspace.require(principal,'terminal-control',conversation.get('project_id'),conversation.get('cwd'))
                if conversation.get('runner_id'): raise ValueError('Shell cron jobs run on this host; choose a local conversation')
            due=next_run(spec,self.clock())
            if due>=self.clock()+grant_days*86400:
                raise ValueError('The first run must be before the execution grant expires')
            goal=grant=created_runbook=None
            try:
                if command:
                    from termx.runbooks import validate_steps
                    created_runbook=self.workspace.agents.create_runbook(name=prompt[:200],project_id=conversation.get('project_id'),steps=validate_steps([{'command':command}]))
                    runbook=self.runbook(principal,conversation_id,created_runbook['id'])
                if runbook and len(runbook['steps'])>resolve_limits(limits)['max_steps']:
                    raise ValueError('Command step count exceeds the run budget')
                goal=self.goal(principal,conversation_id=conversation_id,success_criteria=success_criteria,max_runs=max_runs,limits=limits)
                grant=self.grant(principal,goal_id=goal['id'],expires_at=self.clock()+grant_days*86400,max_runs=max_runs,limits=limits)
                if runbook:
                    grant=self.store.update('delegation',grant['id'],{**grant,'runbook':runbook})
                return self.schedule(principal,goal_id=goal['id'],grant_id=grant['id'],prompt=prompt,spec=spec,
                    missed=missed,overlap=overlap,enabled=True,identifier=identifier,creation_digest=digest)
            except Exception:
                if grant: self.store.delete('delegation',grant['id'])
                if goal: self.store.delete('goal',goal['id'])
                if created_runbook: self.workspace.agents.delete_runbook(created_runbook['id'])
                raise

    def runbook(self,principal,conversation_id,identifier):
        conversation=self.workspace.session(principal,conversation_id,turns=False)
        self.workspace.require(principal,'terminal-control',conversation.get('project_id'),conversation.get('cwd'))
        if conversation.get('runner_id'):
            raise ValueError('Command automations require a conversation on this host; dedicated runners use agent tasks')
        row=self.workspace.agents.get_runbook(identifier)
        if not row: raise ValueError('Saved command automation is unavailable')
        if row.get('project_id')!=conversation.get('project_id'):
            raise ValueError('Choose a command automation and conversation in the same project')
        return {key:row.get(key) for key in ('id','name','project_id','steps')}

    def goal(self,principal,*,conversation_id,success_criteria,max_runs,limits):
        conversation = self.workspace.session(principal,conversation_id,turns=False)
        self.workspace.record(principal,'conversation',conversation_id,scope='agent-run')
        if not success_criteria.strip() or len(success_criteria)>4000 or not 1<=max_runs<=10000:
            raise ValueError('A bounded goal needs success criteria and one to 10000 runs')
        return self.store.create('goal',principal.id,{'conversation_id':conversation_id,'success_criteria':success_criteria,
            'max_runs':max_runs,'limits':resolve_limits(limits),'status':'active','runs_started':0},conversation.get('project_id'))

    def grant(self,principal,*,goal_id,expires_at,max_runs,limits):
        goal = self.workspace.record(principal,'goal',goal_id,scope='agent-control')
        conversation=self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
        limits = resolve_limits(limits)
        if expires_at<=self.clock() or expires_at>self.clock()+90*86400 or not 1<=max_runs<=goal['max_runs']:
            raise ValueError('Delegation must expire within 90 days and stay within goal run budget')
        if any(limits[k]>goal['limits'][k] for k in limits):
            raise ValueError('Delegation cannot exceed goal budgets')
        row = self.store.create('delegation',principal.id,{'goal_id':goal_id,'expires_at':expires_at,
            'max_runs':max_runs,'used_runs':0,'limits':limits,'revoked':False,
            'policy_version':principal.policy_version,'authority':'explicit_unattended_agent_run','execution_target':self.workspace.execution_target(self.workspace.session(principal,conversation['id'],turns=False))},goal.get('project_id'))
        self.store.log(principal.id,'delegation',row['id'],'granted',{'goal_id':goal_id,'expires_at':expires_at})
        return row

    def schedule(self,principal,*,goal_id,grant_id,prompt,spec,missed='skip',overlap='skip',enabled=False,identifier=None,creation_digest=None):
        goal = self.workspace.record(principal,'goal',goal_id,scope='agent-control')
        grant = self.workspace.record(principal,'delegation',grant_id,scope='agent-control')
        self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
        if grant['goal_id']!=goal_id or grant['revoked'] or grant['expires_at']<=self.clock():
            raise PermissionError('Delegated grant does not authorize this goal')
        if missed not in {'skip','once'} or overlap not in {'skip','queue'} or not prompt.strip() or len(prompt)>64000:
            raise ValueError('Invalid schedule prompt or missed/overlap policy')
        due = next_run(spec,self.clock())
        return self.store.create('schedule',principal.id,{'goal_id':goal_id,'grant_id':grant_id,'prompt':prompt,
            'spec':spec,'missed':missed,'overlap':overlap,'enabled':enabled,'next_run':due,'last_run':None,
            'state':'enabled' if enabled else 'disabled','creation_digest':creation_digest,'runbook_id':(grant.get('runbook') or {}).get('id')},goal.get('project_id'),identifier)

    def preview(self,spec,count=5):
        due=self.clock(); dates=[]
        for _ in range(1 if spec.get('kind')=='once' else max(1,min(count,20))):
            due=next_run(spec,due)
            dates.append(due)
        return dates

    def enable(self,principal,identifier,enabled,revision):
        row=self.workspace.record(principal,'schedule',identifier,scope='agent-control')
        grant=self.workspace.record(principal,'delegation',row['grant_id'],scope='agent-control')
        goal=self.workspace.record(principal,'goal',grant['goal_id'])
        self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
        if row.get('archived'):
            raise ValueError('Archived schedules cannot be enabled')
        if enabled and (grant['revoked'] or grant['expires_at']<=self.clock() or goal['status']!='active'):
            raise PermissionError('Renew the delegated grant before enabling')
        return self.store.update('schedule',identifier,{**row,'enabled':enabled,
                'state':'enabled' if enabled else 'disabled','reason':None,'queued':False,
                'next_run':next_run(row['spec'],self.clock()) if enabled else row['next_run']},revision)

    def edit(self, principal, identifier, *, revision, prompt=None, spec=None, missed=None, overlap=None, enabled=None):
        row=self.workspace.record(principal,'schedule',identifier,scope='agent-control')
        if row.get('archived'):
            raise ValueError('Archived schedules cannot be edited')
        changes={key:value for key,value in dict(prompt=prompt,spec=spec,missed=missed,overlap=overlap).items() if value is not None}
        if not changes:
            if enabled is None: raise ValueError('No schedule changes supplied')
            return self.enable(principal,identifier,enabled,revision)
        if enabled is not None: raise ValueError('Save changes before changing schedule state')
        if prompt is not None and (not prompt.strip() or len(prompt)>64000): raise ValueError('Enter a task prompt')
        if missed is not None and missed not in {'skip','once'}: raise ValueError('Invalid missed-run policy')
        if overlap is not None and overlap not in {'skip','queue'}: raise ValueError('Invalid overlap policy')
        if spec is not None: changes.update(next_run=next_run(spec,self.clock()),queued=False)
        return self.store.update('schedule',identifier,{**row,**changes},revision)

    def archive(self,principal,identifier,revision):
        row=self.workspace.record(principal,'schedule',identifier,scope='agent-control')
        return self.store.update('schedule',identifier,{**row,'enabled':False,'archived':True,'state':'archived'},revision)

    async def run_now(self,principal,identifier,revision):
        async with self._lock:
            row=self.workspace.record(principal,'schedule',identifier,scope='agent-control')
            self.workspace.record(principal,'goal',row['goal_id'],scope='agent-run')
            if row['revision']!=revision: raise Conflict('Schedule changed; refresh before running again')
            if row.get('archived'): raise ValueError('Archived schedules cannot run')
            return await self._dispatch(row,manual=True)

    async def control_run(self,principal,identifier,action):
        run=self.workspace.record(principal,'schedule_run',identifier,scope='agent-control')
        if run['status']!='running': raise ValueError('This scheduled run is no longer active')
        if run.get('runbook_run_id'):
            if action=='confirm':
                await self._reconcile_command(run)
                fresh=self.store.get('schedule_run',identifier)
                if fresh['status']!='running': raise PermissionError(fresh.get('reason') or 'Run authority expired')
                return self.workspace.state.agent.runbooks.confirm(run['runbook_run_id'])
            return await self.workspace.state.agent.runbooks.cancel(run['runbook_run_id'])
        if action=='confirm': raise ValueError('Review agent approvals in the conversation')
        await self.cancel_tree(principal,run['task_id'])
        return {'cancelled':True}

    def revoke(self,principal,identifier):
        grant=self.workspace.record(principal,'delegation',identifier,scope='agent-control')
        self.store.update('delegation',identifier,{**grant,'revoked':True})
        for schedule in self.store.list('schedule',principal.id):
            if schedule['grant_id']==identifier:
                self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'grant_revoked'})
        self.store.log(principal.id,'delegation',identifier,'revoked')

    async def cancel_delegation_runs(self,principal,identifier):
        self.workspace.record(principal,'delegation',identifier,scope='agent-control')
        schedules={row['id'] for row in self.store.list('schedule',principal.id) if row['grant_id']==identifier}
        for run in self.store.list('schedule_run',principal.id):
            if run.get('schedule_id') in schedules and run['status']=='running':
                if run.get('runbook_run_id'): await self.workspace.state.agent.runbooks.cancel(run['runbook_run_id'])
                elif run.get('task_id'): await self.cancel_tree(principal,run['task_id'])

    async def cancel_goal(self,principal,identifier):
        goal=self.workspace.record(principal,'goal',identifier,scope='agent-control')
        self.store.update('goal',identifier,{**goal,'status':'cancelled'})
        for schedule in self.store.list('schedule',principal.id):
            if schedule['goal_id']==identifier:
                self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'goal_cancelled'})
        for run in self.store.list('schedule_run',principal.id):
            if run.get('goal_id')==identifier:
                if run.get('runbook_run_id'): await self.workspace.state.agent.runbooks.cancel(run['runbook_run_id'])
                elif run.get('task_id'): await self.cancel_tree(principal,run['task_id'])

    async def cancel_tree(self,principal,task_id):
        self.workspace.record(principal,'task',task_id,scope='agent-control')
        for child in self.workspace.agents.children(task_id):
            meta=self.store.get('task',child['id'])
            if not meta:
                parent=self.store.get('task',task_id)
                self.store.create('task',principal.id,{'conversation_id':parent.get('conversation_id'),
                    'cwd':child['cwd']},parent.get('project_id'),child['id'])
            await self.cancel_tree(principal,child['id'])
        task=self.workspace.agents.get_task(task_id)
        if task and task['status'] in ACTIVE_STATUSES:
            if getattr(self.workspace.state,'runner_agents',None) and self.workspace.state.runner_agents.owns(task):
                self.workspace.state.runner_agents.cancel(task_id)
            elif task.get('engine','internal')!='internal':
                await self.workspace.state.engines.cancel(task_id)
            else:
                self.workspace.state.agent.cancel(task_id)

    async def tick(self):
        if self._lock.locked():
            return
        async with self._lock:
            # Reconcile only known task IDs; approval waits remain waits.
            for run in self.store.list('schedule_run'):
                if run['status']!='running':
                    continue
                if run.get('runbook_run_id'):
                    await self._reconcile_command(run)
                    continue
                if not run.get('task_id'): continue
                task=self.workspace.agents.get_task(run['task_id'])
                if not task:
                    self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention','reason':'Task ledger unavailable'})
                elif task['status'] in ACTIVE_STATUSES and run.get('task_status')!=task['status']:
                    self.store.update('schedule_run',run['id'],{**run,'task_status':task['status']})
                elif task['status'] not in ACTIVE_STATUSES:
                    self.store.update('schedule_run',run['id'],{**run,'status':task['status'],'finished_at':self.clock()})
            for schedule in self.store.list('schedule'):
                if not schedule['enabled'] or schedule['next_run']>self.clock():
                    continue
                await self._dispatch(schedule)

    async def _dispatch(self,schedule,manual=False):
        now=self.clock()
        principal=self.principal_lookup(schedule['owner'])
        goal=self.store.get('goal',schedule['goal_id'])
        grant=self.store.get('delegation',schedule['grant_id'])
        reason=None
        if not principal:
            reason='Principal disabled or unavailable'
        elif not goal or goal['status']!='active':
            reason='Goal inactive'
        elif not grant or grant['revoked'] or grant['expires_at']<=now:
            reason='Delegated grant expired or revoked'
        elif grant['policy_version']!=principal.policy_version:
            reason='Authority changed; review delegated grant again'
        elif grant['used_runs']>=grant['max_runs'] or goal['runs_started']>=goal['max_runs']:
            reason='Run budget exhausted'
        if reason:
            self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'needs_attention','reason':reason})
            if manual: raise PermissionError(reason)
            return
        try:
            self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
            conversation=self.workspace.session(principal,goal['conversation_id'],turns=False)
            if grant.get('runbook') and self.runbook(principal,conversation['id'],grant['runbook']['id'])!=grant['runbook']:
                raise PermissionError('Command automation changed; create a new schedule to authorize its current steps')
            if grant.get('execution_target')!=self.workspace.execution_target(conversation):
                raise PermissionError('Session execution target changed; issue a new explicit delegated grant')
        except Exception as exc:
            self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'needs_attention','reason':str(exc)})
            if manual: raise
            return
        command_active=any(r['status']=='running' and r.get('runbook_run_id') and
            self.store.get('goal',r['goal_id']).get('conversation_id')==conversation['id']
            for r in self.store.list('schedule_run',principal.id))
        if self.workspace.active(conversation['id']) or command_active:
            if manual: raise Conflict('This conversation already has an active turn')
            if schedule['overlap']=='skip':
                self.store.create('schedule_run',schedule['owner'],{'schedule_id':schedule['id'],'goal_id':goal['id'],
                                  'status':'skipped','reason':'Previous conversation turn is active','due':schedule['next_run']},schedule.get('project_id'))
                self.store.update('schedule',schedule['id'],{**schedule,**self._advance(schedule,now)})
            elif not schedule.get('queued'):
                self.store.update('schedule',schedule['id'],{**schedule,'queued':True},schedule['revision'])
            return  # queue keeps one overdue occurrence; never an unbounded backlog.
        grace=60
        if not manual and not schedule.get('queued') and schedule['missed']=='skip' and now-schedule['next_run']>grace:
            self.store.create('schedule_run',schedule['owner'],{'schedule_id':schedule['id'],'goal_id':goal['id'],
                              'status':'skipped','reason':'Missed occurrence','due':schedule['next_run']},schedule.get('project_id'))
            self.store.update('schedule',schedule['id'],{**schedule,**self._advance(schedule,now)})
            return
        try:
            run=self.store.reserve_schedule_run(schedule,now,
                schedule['next_run'] if manual else self._advance(schedule,now)['next_run'],manual=manual)
        except Conflict as exc:
            fresh=self.store.get('schedule',schedule['id'])
            self.store.update('schedule',schedule['id'],{**fresh,'enabled':False,'state':'needs_attention','reason':str(exc)})
            if manual: raise
            return
        if run is None:
            if manual: raise Conflict('Schedule changed; refresh before running again')
            return
        try:
            if grant.get('runbook'):
                snapshot={**grant['runbook'],'steps':[{**step,'_timeout_s':min(grant['limits']['shell_timeout_s'],grant['limits']['max_seconds'])} for step in grant['runbook']['steps']]}
                command=self.workspace.state.agent.runbooks.start(snapshot,conversation['cwd'],profile='host',project_id=conversation.get('project_id'))
                return self.store.update('schedule_run',run['id'],{**run,'status':'running','runbook_run_id':command['id'],'started_at':now,'grant_id':grant['id']})
            task=await self.workspace.send(principal,conversation['id'],prompt=schedule['prompt']+
                '\n\nSuccess criteria:\n'+goal['success_criteria'],request_id='schedule:'+run['id'],limits=grant['limits'],delegation_id=grant['id'])
            self.store.update('schedule_run',run['id'],{**run,'status':'running','task_id':task['id']})
        except Exception as exc:
            self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention','reason':type(exc).__name__})
        return self.store.get('schedule_run',run['id'])

    async def _reconcile_command(self,run):
        command=self.workspace.agents.get_runbook_run(run['runbook_run_id'])
        if not command:
            self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention','reason':'Command run ledger unavailable'})
            return
        if command['status'] in {'running','awaiting_confirmation'}:
            grant=self.store.get('delegation',run['grant_id'])
            principal=self.principal_lookup(run['owner'])
            goal=self.store.get('goal',run['goal_id'])
            try:
                if not grant or grant['revoked'] or grant['expires_at']<=self.clock() or not principal or grant['policy_version']!=principal.policy_version:
                    raise PermissionError('Scheduled command authority expired or changed')
                if self.clock()-run['started_at']>=grant['limits']['max_seconds']:
                    raise PermissionError('Scheduled command time budget exhausted')
                self.runbook(principal,goal['conversation_id'],grant['runbook']['id'])
            except Exception as exc:
                await self.workspace.state.agent.runbooks.cancel(command['id'])
                self.store.update('schedule_run',run['id'],{**run,'status':'cancelled','reason':str(exc),'finished_at':self.clock()})
                return
            if run.get('task_status')!=command['status']:
                self.store.update('schedule_run',run['id'],{**run,'task_status':command['status']})
        else:
            self.store.update('schedule_run',run['id'],{**run,'status':command['status'],'reason':command.get('error'),'finished_at':self.clock(),'step_results':command.get('step_results',[])})

    @staticmethod
    def _advance(schedule,now):
        if schedule['spec']['kind']=='once':
            return {'next_run':None,'enabled':False,'state':'completed','queued':False}
        return {'next_run':next_run(schedule['spec'],now),'queued':False}

    def start(self):
        if not self._worker:
            self._worker=asyncio.create_task(self._loop())

    async def _loop(self):
        while not self._closing:
            try:
                await self.tick()
            except Exception:
                logging.getLogger(__name__).exception('Scheduled task reconciliation failed')
            await asyncio.sleep(5)

    async def close(self):
        self._closing=True
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker,return_exceptions=True)
