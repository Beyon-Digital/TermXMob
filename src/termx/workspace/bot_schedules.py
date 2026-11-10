"""Schedules for one bot.

A bot's schedule is the ordinary delegated schedule (goal, grant and schedule records owned by
``AutomationService``) bound to the bot's conversation. This module only adds what is specific to a
bot: the per-bot cap, pausing with the bot, and parking schedules whose authorization no longer
matches the bot. Editing a bot's prompt or permissions changes what its grant was issued for, so the
owner re-authorizes; a bot cannot rewrite its own authority. Runs that need approval wait for the owner.
"""
from __future__ import annotations

import uuid
from time import time

from termx.agent.limits import resolve_limits
from termx.workspace.bot_triggers import is_event, trigger_kind
from termx.workspace.schedule_spec import next_run
from termx.workspace.store import Conflict

MAX_SCHEDULES = 10
DEFAULT_CRITERIA = 'Finish the task and report briefly what you did and what needs the owner.'
CONFIG_REASON = ("This bot's prompt or permissions changed after you authorized this schedule, so it is paused. "
                 'Re-authorize it to let it run again.')
PERMISSIONS_REASON = 'Your permissions changed after you authorized this schedule, so it stopped. Re-authorize it to let it run again.'
EXPIRED_REASON ='The authorization for this schedule expired. Re-authorize it to let it keep running.'


class BotSchedules:
    def __init__(self, bots):
        self.bots = bots
        self.workspace = bots.workspace
        self.store = bots.store

    # -- plumbing ---------------------------------------------------------------------

    @property
    def automation(self):
        automation = getattr(self.workspace.state, 'automation', None)
        if automation is None:
            raise ValueError('Schedules are unavailable on this host')
        return automation

    def _clock(self):
        try:
            return self.automation.clock()
        except ValueError:
            return time()

    def _require_active(self, bot):
        if bot['state'] != 'active':
            raise Conflict(f'This bot is {bot["state"]}' + ('. Resume it first.' if bot['state'] == 'paused' else ''))

    def _bot(self, principal, bot_id, scope='agent-control'):
        return self.bots._owned(principal, bot_id, scope)

    def _goals(self, bot):
        return {goal['id'] for goal in self.store.list('goal', bot['owner']) if goal.get('conversation_id') == bot['conversation_id']}

    def _rows(self, bot, *, archived=False):
        goals = self._goals(bot)
        return [row for row in self.store.list('schedule', bot['owner'])
                if row.get('goal_id') in goals and bool(row.get('archived')) == archived]

    def _schedule(self, bot, schedule_id):
        row = self.store.get('schedule', str(schedule_id))
        if row is None or row['owner'] != bot['owner'] or row.get('goal_id') not in self._goals(bot):
            raise KeyError(schedule_id)
        return row

    @staticmethod
    def _due(schedule, after):
        """The next time a schedule is due. An event schedule has no clock: something has to happen first."""
        return None if is_event(schedule) else next_run(schedule['spec'], after)

    def _set(self, schedule_id, **fields):
        row = self.store.get('schedule', schedule_id)
        return self.store.update('schedule', schedule_id, {**row, **fields})

    def checked(self, principal, bot, rows):
        """``(schedule, grant, reason)`` for each row; ``reason`` says why the owner must authorize it again, or is None.
        The bot's session is read once for the whole list."""
        target, out = None, []
        for row in rows:
            grant = self.store.get('delegation', row['grant_id']) or {}
            reason = None
            if row.get('state') == 'grant_revoked':
                pass  # the owner revoked it on purpose; creating a new schedule is the way back
            elif row.get('state') == 'needs_reauthorization':
                reason = row.get('reason') or CONFIG_REASON
            elif grant and not grant.get('revoked'):
                if target is None:
                    target = self.workspace.execution_target(self.workspace.session(principal, bot['conversation_id'], turns=False))
                if grant.get('execution_target') != target:
                    reason = CONFIG_REASON
                elif grant.get('policy_version') != principal.policy_version:
                    reason = PERMISSIONS_REASON
                elif grant['expires_at'] <= self._clock():
                    reason = EXPIRED_REASON
            out.append((row, grant, reason))
        return out

    # -- reading -------------------------------------------------------------------------

    def view(self, principal, bot, schedule):
        return self._view(bot, *self.checked(principal, bot, [schedule])[0])

    def _view(self, bot, schedule, grant, reason):
        goal = self.store.get('goal', schedule['goal_id']) or {}
        return {
            'id': schedule['id'], 'revision': schedule['revision'], 'bot_id': bot['id'], 'conversation_id': bot['conversation_id'],
            'goal_id': schedule['goal_id'], 'grant_id': schedule['grant_id'], 'prompt': schedule['prompt'], 'spec': schedule['spec'],
            'missed': schedule['missed'], 'overlap': schedule['overlap'], 'enabled': bool(schedule['enabled']),
            'state': schedule['state'], 'reason': reason or schedule.get('reason'), 'next_run': schedule.get('next_run'),
            'last_run': schedule.get('last_run'), 'queued': bool(schedule.get('queued')),
            'success_criteria': goal.get('success_criteria', ''), 'max_runs': goal.get('max_runs', 0),
            'runs_started': goal.get('runs_started', 0), 'limits': goal.get('limits') or {},
            'grant_expires_at': grant.get('expires_at'), 'grant_used_runs': grant.get('used_runs', 0),
            'needs_reauthorization': reason is not None, 'paused_by_bot': schedule.get('resume_when') == 'resume',
            'computer_permission': grant.get('computer_permission'),
            'trigger': schedule.get('trigger') or {'kind': 'time'},
            'report_to': schedule.get('report_to') or {'kind': 'orchestrator', 'id': None},
            'webhook_path': f'/hooks/bots/{bot["id"]}/{schedule["id"]}' if trigger_kind(schedule) == 'webhook' else None,
            'created_at': schedule['created_at'], 'updated_at': schedule['updated_at']}

    def list(self, principal, bot_id):
        bot = self._bot(principal, bot_id, 'agent-view')
        rows = sorted(self._rows(bot), key=lambda row: row['created_at'])
        return [self._view(bot, *item) for item in self.checked(principal, bot, rows)]

    def runs(self, principal, bot_id, limit=50):
        bot = self._bot(principal, bot_id, 'agent-view')
        goals = self._goals(bot)
        rows = [run for run in self.store.list('schedule_run', bot['owner']) if run.get('goal_id') in goals]
        return [self.run_view(bot, run) for run in rows[:max(1, min(int(limit), 200))]]

    @staticmethod
    def run_view(bot, run):
        view = {key: run.get(key) for key in ('id', 'schedule_id', 'goal_id', 'status', 'reason', 'task_id', 'due', 'task_status',
                                              'started_at', 'finished_at', 'created_at', 'updated_at')}
        return {**view, 'bot_id': bot['id'], 'manual': bool(run.get('manual')), 'acknowledged': bool(run.get('acknowledged')),
                'trigger_kind': run.get('trigger_kind') or ('manual' if run.get('manual') else 'time')}

    # -- rules the scheduler applies on every path, REST included ---------------------------

    def admit(self, conversation_id, *, shell=False, browser=False, computer=False, event=False):
        """Called by ``AutomationService.create``: a bot takes new schedules only while active and under its cap, and
        only as agent tasks. A saved shell command or an unattended browser grant would act outside the bot's ceiling.
        A computer permission is accepted only when the bot's ceiling already allows computer control."""
        bot = self.bots.for_conversation(conversation_id)
        if bot is None:
            return
        if shell or browser:
            raise ValueError("A bot's schedules run the bot itself. Shell commands and browser access are not part of its ceiling.")
        self._require_active(bot)
        if computer:
            if event:
                raise ValueError('Computer control is only for timed schedules. A webhook, message or file change must not start a run that drives the desktop.')
            self._require_computer(bot)
        if len(self._rows(bot)) >= MAX_SCHEDULES:
            raise Conflict(f'A bot can have at most {MAX_SCHEDULES} schedules; archive one first')

    def _computer_refusal(self, bot):
        """Default deny: the ceiling must list the computer tool and not deny computer control, the bot must use the tool,
        and its conversation must be able to change things. Returns why not, or None."""
        ceiling = bot.get('ceiling') or {}
        if 'computer' not in ceiling.get('tools', ()) or 'computer.control' in ceiling.get('deny_capabilities', ()):
            return "This bot's permissions do not allow computer control. Allow it under Permissions first."
        if 'computer' not in self.bots._load(bot).tools:
            return 'This bot does not use the computer tool. Add it under Permissions first.'
        if (self.bots.agents.get_conversation(bot['conversation_id'], include_turns=False) or {}).get('mode') != 'agent':
            return 'This bot is in read-only mode, so it cannot control the computer. Add a tool that changes things under Permissions first.'
        return None

    def _require_computer(self, bot):
        reason = self._computer_refusal(bot)
        if reason:
            raise ValueError(reason)

    def drop_computer_permissions(self, bot):
        """The ceiling narrowed: a stored computer permission the bot may no longer use is removed from its schedules' grants."""
        if self._computer_refusal(bot) is None:
            return
        for row in self._rows(bot):
            grant = self.store.get('delegation', row.get('grant_id') or '')
            if grant and 'computer_permission' in grant:
                self.store.update('delegation', grant['id'], {key: value for key, value in grant.items() if key != 'computer_permission'})
                self.store.log(bot['owner'], 'delegation', grant['id'], 'computer_permission_removed', {'schedule_id': row['id'], 'reason': 'ceiling'})

    def require_current(self, principal, schedule):
        """Called by ``AutomationService.enable`` and ``run_now``: a paused bot, or a schedule authorized for an older
        prompt or permissions, does not start until the owner resumes or re-authorizes it."""
        goal = self.store.get('goal', schedule['goal_id']) or {}
        bot = self.bots.for_conversation(goal.get('conversation_id'))
        if bot is None:
            return
        self._require_active(bot)
        if self.checked(principal, bot, [schedule])[0][2]:
            raise PermissionError("Re-authorize this bot's schedules first")

    # -- changing --------------------------------------------------------------------------

    def create(self, principal, bot_id, *, request_id, prompt, spec=None, success_criteria=None, max_runs=30, grant_days=30,
               missed='skip', overlap='skip', limits=None, trigger=None, report_to=None, computer_permission=None):
        """A timed schedule (``spec``) or an event schedule (``trigger``). A webhook schedule starts off, and its secret is in the
        result of this call only; ask for a new one with ``rotate_secret``."""
        bot = self._bot(principal, bot_id)
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise ValueError('A request ID of 8 to 128 characters makes a retry safe')
        trigger = self.bots.triggers.normalize(bot, trigger)
        if trigger['kind'] != 'time':
            spec = {'kind': 'event'}
        if not isinstance(prompt, str) or not isinstance(spec, dict) or not isinstance(max_runs, int) or not isinstance(grant_days, int):
            raise ValueError('A schedule needs a task, a when and whole-number limits')
        report = self.bots._report_target(principal, report_to) if report_to is not None else None
        if success_criteria is not None and not isinstance(success_criteria, str):
            raise ValueError('Success criteria must be text')
        if not 1 <= grant_days <= 90:
            raise ValueError('An authorization lasts 1 to 90 days')
        self.workspace.require(principal, 'agent-run')
        automation = self.automation
        with self.store.lock:
            agent = self.bots._load(bot)
            replay = self.store.get('schedule', uuid.uuid5(uuid.NAMESPACE_URL, principal.id + ':schedule:' + request_id).hex) is not None
            row = automation.create(
                principal, request_id=request_id, conversation_id=bot['conversation_id'],
                success_criteria=(success_criteria or '').strip() or DEFAULT_CRITERIA, max_runs=max_runs,
                limits=resolve_limits(limits, defaults=dict(agent.limits)), prompt=prompt, spec=spec, grant_days=grant_days,
                missed=missed, overlap=overlap, trigger=trigger if trigger['kind'] != 'time' else None, report_to=report,
                enabled=trigger['kind'] != 'webhook', computer_permission=computer_permission)
            secret = None if replay or trigger['kind'] != 'webhook' else self.bots.triggers.issue_secret(bot['owner'], row['id'])
        self.store.log(principal.id, 'schedule', row['id'], 'bot_schedule_created', {'bot_id': bot['id'], 'trigger': trigger['kind']})
        view = self.view(principal, bot, row)
        return {**view, 'webhook_secret': secret} if secret else view

    def edit(self, principal, bot_id, schedule_id, *, revision, prompt=None, spec=None, missed=None, overlap=None,
             trigger=None, report_to=None, computer_permission=None, clear_computer_permission=False):
        """``computer_permission`` replaces the schedule's computer permission; ``clear_computer_permission`` removes it."""
        bot = self._bot(principal, bot_id)
        row = self._schedule(bot, schedule_id)
        if computer_permission is not None and clear_computer_permission:
            raise ValueError('Set the computer permission or clear it, not both')
        if computer_permission is not None or clear_computer_permission:
            if computer_permission is not None:
                self._require_active(bot)
                self._require_computer(bot)
            changed = [value for value in (prompt, spec, missed, overlap, trigger, report_to) if value is not None]
            if changed:
                raise ValueError('Change the computer permission on its own, then edit the schedule')
            row = self.automation.set_computer_permission(principal, schedule_id, revision=revision,
                                                          selection=None if clear_computer_permission else computer_permission)
            return self.view(principal, bot, row)
        extras = {}
        if trigger is not None:
            extras['trigger'] = self.bots.triggers.update(bot, row.get('trigger'), trigger)
        if report_to is not None:
            extras['report_to'] = self.bots._report_target(principal, report_to)
        if any(value is not None for value in (prompt, spec, missed, overlap)) or not extras:
            row = self.automation.edit(principal, schedule_id, revision=revision, prompt=prompt, spec=spec, missed=missed, overlap=overlap)
            revision = row['revision']
        elif row.get('archived'):
            raise ValueError('Archived schedules cannot be edited')
        if extras:
            row = self.store.update('schedule', schedule_id, {**self.store.get('schedule', schedule_id), **extras}, revision)
        return self.view(principal, bot, row)

    def rotate_secret(self, principal, bot_id, schedule_id):
        """A new webhook secret, shown in this result only. The old one stops working at once."""
        bot = self._bot(principal, bot_id)
        row = self._schedule(bot, schedule_id)
        if trigger_kind(row) != 'webhook' or row.get('archived'):
            raise ValueError('Only a webhook trigger has a secret')
        secret = self.bots.triggers.issue_secret(bot['owner'], schedule_id)
        return {**self.view(principal, bot, row), 'webhook_secret': secret}

    def enable(self, principal, bot_id, schedule_id, enabled, revision):
        bot = self._bot(principal, bot_id)
        self._schedule(bot, schedule_id)
        updated = self.automation.enable(principal, schedule_id, bool(enabled), revision)
        return self.view(principal, bot, self._set(updated['id'], resume_when=None))

    def archive(self, principal, bot_id, schedule_id, revision):
        bot = self._bot(principal, bot_id)
        self._schedule(bot, schedule_id)
        view = self.view(principal, bot, self.automation.archive(principal, schedule_id, revision))
        self.bots.triggers.drop_secret(schedule_id)
        return view

    async def run_now(self, principal, bot_id, schedule_id, revision):
        bot = self._bot(principal, bot_id, 'agent-run')
        self._schedule(bot, schedule_id)
        run = await self.automation.run_now(principal, schedule_id, revision)
        return self.run_view(bot, run)

    async def revoke(self, principal, bot_id, grant_id):
        bot = self._bot(principal, bot_id)
        if not any(row['grant_id'] == grant_id for row in self._rows(bot)):
            raise KeyError(grant_id)
        self.automation.revoke(principal, grant_id)
        await self.automation.cancel_delegation_runs(principal, grant_id)

    def acknowledge_run(self, principal, bot_id, run_id):
        bot = self._bot(principal, bot_id)
        run = self.store.get('schedule_run', str(run_id))
        if run is None or run['owner'] != bot['owner'] or run.get('goal_id') not in self._goals(bot):
            raise KeyError(run_id)
        return self.run_view(bot, self.store.update('schedule_run', run['id'], {**run, 'acknowledged': True}))

    # -- authorization ------------------------------------------------------------------------

    def reauthorize(self, principal, bot_id, grant_days=30):
        """Issue a fresh delegation for every schedule whose authorization no longer matches the bot.

        Nothing changes unless all of them can be renewed. A schedule that was running before it was
        parked runs again; one the owner had turned off stays off.
        """
        bot = self._bot(principal, bot_id)
        if not isinstance(grant_days, int) or isinstance(grant_days, bool) or not 1 <= grant_days <= 90:
            raise ValueError('An authorization lasts 1 to 90 days')
        self.workspace.require(principal, 'agent-run')
        self._require_active(bot)
        automation = self.automation
        with self.store.lock:
            agent_limits = resolve_limits(None, defaults=dict(self.bots._load(bot).limits))
            todo = []
            for row, old, reason in self.checked(principal, bot, self._rows(bot)):
                if reason:
                    goal = self.store.get('goal', row['goal_id'])
                    if not goal or goal['status'] != 'active' or goal['runs_started'] >= goal['max_runs']:
                        raise ValueError('A schedule has no runs left to authorize. Archive it and create a new one.')
                    todo.append((row, old, goal))
            now, renewed = self._clock(), []
            for row, old, goal in todo:
                # The new grant is never wider than the bot's current budget, so narrowing a bot sticks.
                limits = {key: min(value, agent_limits.get(key, value)) for key, value in goal['limits'].items()}
                grant = automation.grant(principal, goal_id=goal['id'], expires_at=now + grant_days * 86400,
                                         max_runs=goal['max_runs'] - goal['runs_started'], limits=limits)
                resume = bool(row['enabled']) or row.get('resume_when') == 'reauthorize' or row['state'] == 'needs_attention'
                changes = {'grant_id': grant['id'], 'resume_when': None, 'reason': None, 'queued': False,
                           'enabled': False, 'state': 'disabled'}
                if resume:
                    try:
                        changes.update(next_run=self._due(row, now), enabled=True, state='enabled')
                    except ValueError:
                        changes['reason'] = 'The one-time run time has passed. Edit the time to run it again.'
                renewed.append(self._set(row['id'], **changes))
                self.store.log(principal.id, 'delegation', grant['id'], 'reauthorized', {'bot_id': bot['id'], 'replaces': old.get('id')})
            replaced = {old['id'] for _row, old, _goal in todo if old}
            still_used = {row['grant_id'] for row in self._rows(bot)}
            for identifier in replaced - still_used:
                grant = self.store.get('delegation', identifier)
                self.store.update('delegation', identifier, {**grant, 'revoked': True})
        return [self.view(principal, bot, self.store.get('schedule', row['id'])) for row in renewed]

    # -- following the bot ----------------------------------------------------------------------

    def _stop(self, bot, *, state, reason, resume_when):
        """Turn off the schedules that are on (or waiting for the bot to resume), remembering what brings them back."""
        for row in self._rows(bot):
            if row['enabled'] or (resume_when == 'reauthorize' and row.get('resume_when') == 'resume'):
                self._set(row['id'], enabled=False, state=state, reason=reason, resume_when=resume_when, queued=False)

    def park(self, bot):
        """The bot's prompt or permissions changed: stop its schedules until the owner re-authorizes them."""
        self._stop(bot, state='needs_reauthorization', reason=CONFIG_REASON, resume_when='reauthorize')

    def pause(self, bot):
        """Turn off the schedules that are on, remembering which, so resuming the bot restores exactly those."""
        self._stop(bot, state='disabled', reason=None, resume_when='resume')

    def resume(self, principal, bot):
        for row, _grant, reason in self.checked(principal, bot, [row for row in self._rows(bot) if row.get('resume_when') == 'resume']):
            if reason:
                self._set(row['id'], state='needs_reauthorization', reason=reason, resume_when='reauthorize')
                continue
            try:
                self._set(row['id'], enabled=True, state='enabled', reason=None, resume_when=None, queued=False,
                          next_run=self._due(row, self._clock()))
            except ValueError as exc:
                self._set(row['id'], state='disabled', reason=str(exc), resume_when=None)
