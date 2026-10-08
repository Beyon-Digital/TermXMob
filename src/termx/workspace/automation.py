"""Durable delegated schedules driven by the daemon rather than UI lifetime."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from time import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from termx.agent.limits import resolve_limits
from termx.agent.store import ACTIVE_STATUSES
from termx.workspace.store import Conflict


def next_run(spec: dict, after: float) -> float:
    """Daily wall-clock schedules skip nonexistent DST times, run folds once."""
    if spec.get('kind') == 'interval':
        seconds = spec.get('seconds')
        if isinstance(seconds,bool) or not isinstance(seconds,int) or not 60<=seconds<=31_536_000:
            raise ValueError('Interval must be 60 seconds to one year')
        return after+seconds
    if spec.get('kind') != 'daily':
        raise ValueError('Supported schedule kinds are daily and interval')
    try:
        zone = ZoneInfo(spec['timezone'])
        hour, minute = map(int,spec['time'].split(':'))
        if not (0<=hour<=23 and 0<=minute<=59):
            raise ValueError
    except (KeyError,ValueError,ZoneInfoNotFoundError):
        raise ValueError('Daily schedule requires an IANA timezone and HH:MM') from None
    local = datetime.fromtimestamp(after,zone)
    for offset in range(370):
        day = local.date()+timedelta(days=offset)
        candidate = datetime(day.year,day.month,day.day,hour,minute,tzinfo=zone,fold=0)
        stamp = candidate.timestamp()
        roundtrip = datetime.fromtimestamp(stamp,zone)
        if roundtrip.hour==hour and roundtrip.minute==minute and roundtrip.date()==day and stamp>after:
            return stamp
    raise ValueError('Cannot determine next run')


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

    def schedule(self,principal,*,goal_id,grant_id,prompt,spec,missed='skip',overlap='skip',enabled=False):
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
            'state':'enabled' if enabled else 'disabled'},goal.get('project_id'))

    def preview(self,spec,count=5):
        due=self.clock(); dates=[]
        for _ in range(max(1,min(count,20))):
            due=next_run(spec,due)
            dates.append(due)
        return dates

    def enable(self,principal,identifier,enabled,revision):
        row=self.workspace.record(principal,'schedule',identifier,scope='agent-control')
        grant=self.workspace.record(principal,'delegation',row['grant_id'],scope='agent-control')
        goal=self.workspace.record(principal,'goal',grant['goal_id'])
        self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
        if enabled and (grant['revoked'] or grant['expires_at']<=self.clock()):
            raise PermissionError('Renew the delegated grant before enabling')
        return self.store.update('schedule',identifier,{**row,'enabled':enabled,
                'state':'enabled' if enabled else 'disabled','next_run':next_run(row['spec'],self.clock())},revision)

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
            if run.get('schedule_id') in schedules and run.get('task_id') and run['status']=='running':
                await self.cancel_tree(principal,run['task_id'])

    async def cancel_goal(self,principal,identifier):
        goal=self.workspace.record(principal,'goal',identifier,scope='agent-control')
        self.store.update('goal',identifier,{**goal,'status':'cancelled'})
        for schedule in self.store.list('schedule',principal.id):
            if schedule['goal_id']==identifier:
                self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'goal_cancelled'})
        for run in self.store.list('schedule_run',principal.id):
            if run.get('goal_id')==identifier and run.get('task_id'):
                await self.cancel_tree(principal,run['task_id'])

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
                if run['status']!='running' or not run.get('task_id'):
                    continue
                task=self.workspace.agents.get_task(run['task_id'])
                if not task:
                    self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention','reason':'Task ledger unavailable'})
                elif task['status'] not in ACTIVE_STATUSES:
                    self.store.update('schedule_run',run['id'],{**run,'status':task['status'],'finished_at':self.clock()})
            for schedule in self.store.list('schedule'):
                if not schedule['enabled'] or schedule['next_run']>self.clock():
                    continue
                await self._dispatch(schedule)

    async def _dispatch(self,schedule):
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
            return
        try:
            self.workspace.record(principal,'conversation',goal['conversation_id'],scope='agent-run')
            conversation=self.workspace.session(principal,goal['conversation_id'],turns=False)
            if grant.get('execution_target')!=self.workspace.execution_target(conversation):
                raise PermissionError('Session execution target changed; issue a new explicit delegated grant')
        except Exception as exc:
            self.store.update('schedule',schedule['id'],{**schedule,'enabled':False,'state':'needs_attention','reason':str(exc)})
            return
        if self.workspace.active(conversation['id']):
            if schedule['overlap']=='skip':
                self.store.create('schedule_run',schedule['owner'],{'schedule_id':schedule['id'],'goal_id':goal['id'],
                                  'status':'skipped','reason':'Previous conversation turn is active','due':schedule['next_run']},schedule.get('project_id'))
                self.store.update('schedule',schedule['id'],{**schedule,'next_run':next_run(schedule['spec'],now)})
            return  # queue keeps one overdue occurrence; never an unbounded backlog.
        grace=60
        if schedule['missed']=='skip' and now-schedule['next_run']>grace:
            self.store.create('schedule_run',schedule['owner'],{'schedule_id':schedule['id'],'goal_id':goal['id'],
                              'status':'skipped','reason':'Missed occurrence','due':schedule['next_run']},schedule.get('project_id'))
            self.store.update('schedule',schedule['id'],{**schedule,'next_run':next_run(schedule['spec'],now)})
            return
        try:
            run=self.store.reserve_schedule_run(schedule,now,next_run(schedule['spec'],now))
        except Conflict as exc:
            fresh=self.store.get('schedule',schedule['id'])
            self.store.update('schedule',schedule['id'],{**fresh,'enabled':False,'state':'needs_attention','reason':str(exc)})
            return
        if run is None:
            return
        try:
            task=await self.workspace.send(principal,conversation['id'],prompt=schedule['prompt']+
                '\n\nSuccess criteria:\n'+goal['success_criteria'],request_id='schedule:'+run['id'],limits=grant['limits'],delegation_id=grant['id'])
            self.store.update('schedule_run',run['id'],{**run,'status':'running','task_id':task['id']})
        except Exception as exc:
            self.store.update('schedule_run',run['id'],{**run,'status':'needs_attention','reason':type(exc).__name__})

    def start(self):
        if not self._worker:
            self._worker=asyncio.create_task(self._loop())

    async def _loop(self):
        while not self._closing:
            try:
                await self.tick()
            except Exception:
                # Daemon stays alive; no failed action is granted consent.
                pass
            await asyncio.sleep(5)

    async def close(self):
        self._closing=True
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker,return_exceptions=True)
