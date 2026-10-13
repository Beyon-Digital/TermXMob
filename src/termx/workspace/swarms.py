"""Explicit, bounded desktop team assembly over the existing delegation runtime."""
from __future__ import annotations

import hashlib
import json
import asyncio

from termx.agent.providers import ProviderCall
from termx.workspace.store import Conflict


async def assemble(workspace, principal, task_id, *, request_id, members):
    metadata = workspace.record(principal, 'task', task_id, scope='agent-run')
    workspace.record(principal, 'task', task_id, scope='agent-control')
    if metadata.get('runner_id'):
        raise ValueError('Team assembly currently requires a local internal coordinator')
    manager = workspace.state.agent
    digest = hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()
    async with manager._spawn_locks[task_id]:
        # Durable request identity prevents duplicate workers after a lost HTTP
        # response. An interrupted dispatch requires inspection, never replay.
        events = workspace.agents.events(task_id, event_types=('swarm.requested', 'swarm.created'))
        previous = [e['payload'] for e in events if e['type'] == 'swarm.requested'
                    and e['payload'].get('request_id') == request_id]
        if previous:
            if previous[-1]['digest'] != digest:
                raise Conflict('Team request changed; use a new request ID')
            finished = [e['payload'] for e in events if e['type'] == 'swarm.created'
                        and e['payload'].get('request_id') == request_id]
            if finished:
                return finished[-1]
            raise Conflict('Team dispatch was interrupted. Inspect the task tree before creating more workers')
        task = workspace.agents.get_task(task_id)
        if not task or task.get('parent_id') or task.get('engine', 'internal') != 'internal':
            raise ValueError('Choose a root task using the internal engine')
        if task['status'] not in {'planning', 'running', 'awaiting_approval', 'paused'} or task.get('mode') == 'ask':
            raise Conflict('Choose an active Agent mode coordinator before creating a team')
        labels = [m['agent'].strip().casefold() for m in members]
        if not members or len(members) > 8 or len(set(labels)) != len(labels) or any(not label for label in labels):
            raise ValueError('Choose one to eight workers with unique non-empty names')
        if any(not m['task'].strip() for m in members):
            raise ValueError('Every worker needs a task')
        handles = manager._subagent_handles(task_id)
        limits = task['limits']
        active = sum(not h.done.is_set() for h in handles.values())
        if active + len(members) > limits['max_parallel_subagents'] or len(handles) + len(members) > limits['max_subagents_total']:
            raise Conflict('The team exceeds the coordinator’s remaining parallel or total worker allowance')
        cancel = manager._cancel.get(task_id) or asyncio.Event()
        if cancel.is_set():
            raise Conflict('The coordinator is not running on this host; recover it before creating a team')
        manager.emit_external(task_id, 'swarm.requested', {'request_id': request_id, 'digest': digest})
        results = []
        for index, member in enumerate(members):
            try:
                workspace.record(principal, 'task', task_id, scope='agent-run')
                call = ProviderCall(type='function', call_id=f'swarm:{request_id}:{index}',
                                    name='spawn_subagent', arguments=member)
                outcome = await manager._spawn_subagent_locked(task_id, task, call, cancel)
            except asyncio.CancelledError:
                if not cancel.is_set():raise
                outcome = {'ok': False, 'error': 'Coordinator cancelled during worker creation'}
            except (ValueError, OSError, KeyError, PermissionError) as exc:
                outcome = {'ok': False, 'error': str(exc)}
            results.append({'agent': member['agent'], 'task': member['task'], **outcome})
            if not outcome.get('ok'):
                # Stop on partial failure; surface every undispatched member.
                results.extend({'agent': m['agent'], 'task': m['task'], 'ok': False,
                                'error': 'Not started because a previous worker could not be created'}
                               for m in members[index + 1:])
                break
        result = {'request_id': request_id, 'members': results, 'ok': all(r['ok'] for r in results)}
        manager.emit_external(task_id, 'swarm.created', result)
        ids = [r['task_id'] for r in results if r.get('task_id')]
        if ids:
            try:
                manager.steer(task_id, 'The user assembled these sub-agents: ' + ', '.join(ids)
                              + '. Use subagent_status and await_subagents to inspect and collect their results. Review isolated edits before applying them.')
            except ValueError:
                pass  # The coordinator can finish while isolation is prepared.
        workspace.store.log(principal.id, 'task', task_id, 'swarm.created', {'request_id': request_id, 'task_ids': ids})
        return result
