"""The "needs you" inbox: everything a bot is waiting on the owner for.

Five sources, all read from records that already exist:

* pending approvals on the bot's tasks (a scheduled run that needs approval parks here, it is never auto-approved);
* scheduled runs that ended as ``needs_attention`` and have not been acknowledged;
* schedules parked until the owner re-authorizes them;
* memory notes the bot proposed, waiting to be kept or discarded;
* messages: unread ones a bot sent to the owner, and any message a loop or rate limit held back.

Every text field is data written by a model or an earlier run, so it is shown, never followed.
"""
from __future__ import annotations

from fastapi import HTTPException

from termx.agent.policy import redact

LIMIT = 100


def _short(value, size=300):
    return redact(str(value or ''))[:size]


def _approvals(service, bot):
    items = []
    for task in service.workspace.active(bot['conversation_id']):
        scheduled = bool((service.store.get('task', task['id']) or {}).get('delegation_id'))
        for approval in service.agents.approvals(task['id']):
            if approval['status'] != 'pending':
                continue
            payload = approval.get('payload') or {}
            items.append({'id': 'approval:' + approval['id'], 'kind': 'approval', 'task_id': task['id'], 'approval_id': approval['id'],
                          'title': _short(payload.get('title') or 'Approval needed', 160),
                          'detail': _short(payload.get('consequence') or payload.get('detail') or payload.get('summary')),
                          'scheduled': scheduled, 'created_at': approval['created_at']})
    return items


def _runs(service, bot):
    goals = service.schedules._goals(bot)
    return [{'id': 'run:' + run['id'], 'kind': 'run', 'run_id': run['id'], 'schedule_id': run.get('schedule_id'), 'task_id': run.get('task_id'),
             'title': 'A scheduled run needs your attention', 'detail': _short(run.get('reason')), 'scheduled': True,
             'created_at': run['updated_at']}
            for run in service.store.list('schedule_run', bot['owner'])
            if run.get('goal_id') in goals and run['status'] == 'needs_attention' and not run.get('acknowledged')]


def _parked(service, principal, bot):
    schedules = service.schedules
    return [{'id': 'schedule:' + row['id'], 'kind': 'schedule', 'schedule_id': row['id'],
             'title': 'A schedule is paused until you re-authorize it', 'detail': _short(reason),
             'scheduled': True, 'created_at': row['updated_at']}
            for row, _grant, reason in schedules.checked(principal, bot, schedules._rows(bot)) if reason]


def _memories(service, principal, bot):
    try:
        notes = service.workspace.memories(principal, project_id=bot.get('project_id'), agent_id=bot['custom_agent_id'], state='proposed')
    except (PermissionError, HTTPException):
        return []
    return [{'id': 'memory:' + note['id'], 'kind': 'memory', 'memory_id': note['id'], 'title': 'The bot proposed a memory note',
             'detail': _short(note.get('content'), 500), 'scheduled': False, 'created_at': note['created_at']} for note in notes]


def _messages(service, _principal, bot, rows=None):
    return [{'id': 'message:' + note['id'], 'kind': 'message', 'message_id': note['id'], 'scheduled': False,
             'title': 'A message was held' if note['status'] == 'parked' else _short('Message: ' + note['subject'], 160),
             'detail': _short(f'{note["reason"]} Subject: {note["subject"]}' if note['status'] == 'parked' else note['body'], 500),
             'created_at': note['created_at']} for note in service.messages.attention(bot, rows)]


def items_for(service, principal, bot, rows=None):
    """Everything the bot waits on. ``rows`` are the owner's messages when the caller already loaded them."""
    if bot['state'] == 'archived':
        return []
    found = [*_approvals(service, bot), *_runs(service, bot), *_parked(service, principal, bot),
             *_memories(service, principal, bot), *_messages(service, principal, bot, rows)]
    base = {'bot_id': bot['id'], 'bot_name': bot['name'], 'task_id': None, 'approval_id': None, 'run_id': None,
            'schedule_id': None, 'memory_id': None, 'message_id': None}
    return [{**base, **item} for item in sorted(found, key=lambda item: item['created_at'] or 0, reverse=True)]


def inbox(service, principal, bot_id=None):
    """Items for one bot, or for all of the owner's active bots, newest first."""
    service.workspace.require(principal, 'agent-view')
    if bot_id is not None:
        bots = [service._owned(principal, bot_id)]
    else:
        bots = []
        for row in service.store.list('bot', principal.id):
            if row['state'] != 'archived':
                try:
                    bots.append(service._owned(principal, row['id']))
                except (PermissionError, KeyError, HTTPException):  # a bot the caller can no longer see is left out
                    continue
    items = [item for bot in bots for item in items_for(service, principal, bot)]
    return sorted(items, key=lambda item: item['created_at'] or 0, reverse=True)[:LIMIT]
