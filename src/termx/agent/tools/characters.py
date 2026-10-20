"""Character authoring and expressive companion intents through task authority."""
import asyncio
from fastapi import HTTPException

from termx.agent.policy import PolicyDecision
from .registry import ToolOutcome, ToolSpec


def decide(call, ctx):
    if ctx.read_only and call.name != 'character_validate':
        return PolicyDecision(False, False, 'Ask mode is read-only', 'Character changes require Agent mode')
    if call.name == 'character_generate':
        return PolicyDecision(False, True, 'Generate character artwork',
                              'Use the selected image provider and model; provider charges may apply. ' +
                              str({key: call.arguments.get(key) for key in ('provider_id', 'model', 'phase', 'clip')}))
    return PolicyDecision(True, False, 'Character presentation', 'Changes only this agent’s companion or owned character drafts')


async def execute(call, ctx):
    service = getattr(ctx.manager, 'characters', None)
    if not service:
        return ToolOutcome({'refused': True, 'error': 'Character service unavailable'})
    try:
        row = service.state.workspace.store.get('task', ctx.task_id)
        if not row:
            raise PermissionError('Task is not enrolled')
        origin = service.state.browser.records.get('agent-task-authority', ctx.task_id)
        if not origin:
            raise PermissionError('Originating managed session is unavailable')
        principal = service.state.identity.execution_session(origin['session_id'])
        if not principal:
            raise PermissionError('Originating managed session is unavailable')
        principal = principal.principal
        def authority():
            live = service.state.identity.execution_session(origin['session_id'])
            if (ctx.cancel.is_set() or not live or live.principal.id != row['owner'] or
                origin['principal_id'] != live.principal.id or origin['policy_version'] != live.principal.policy_version or
                (origin.get('project_id') or None) != row.get('project_id')):
                raise PermissionError('Originating managed session was revoked or changed')
            service.state.workspace.record(live.principal, 'task', ctx.task_id,
                                            scope='agent-view' if call.name == 'character_validate' else 'agent-run')
            service.state.workspace.validate_task_execution(ctx.task_id)
        authority()
        args = call.arguments
        if ctx.read_only and call.name != 'character_validate':
            raise PermissionError('Character changes require Agent mode')
        if call.name == 'companion_act':
            return ToolOutcome({'action': service.act(ctx.task_id, args)})
        if call.name == 'character_validate':
            manifest, _ = service.pack(principal.id, args['pack_id'], args.get('revision'))
            return ToolOutcome({'valid': True, 'pack_id': manifest['id'], 'revision': manifest['revision'],
                                'clips': list(manifest['clips']), 'fallback_clips': manifest.get('fallback_clips', [])})
        if call.name == 'character_build':
            from termx.characters.http import Build
            body = Build.model_validate(args)
            artifact = service.state.artifacts.get(principal.id, body.artifact_id)
            if artifact.get('project') != row.get('project_id'):
                raise PermissionError('Image belongs to another project')
            result = await asyncio.to_thread(service.build, principal.id, **body.model_dump(), authority=authority)
            return ToolOutcome({'pack': result})
        if call.name == 'character_generate':
            from termx.characters.http import Generate
            # Billing consent is the tool approval for these exact arguments.
            import hashlib
            request_id = 'tool-' + hashlib.sha256((ctx.task_id + ':' + call.call_id).encode()).hexdigest()
            body = Generate.model_validate({**args, 'request_id': request_id,
                                            'project_id': row.get('project_id'), 'acknowledge_billing': True})
            operation = service.generate(principal.id, body.model_dump(), authority=authority)
            job = service._job_index.get((principal.id, operation['id']))
            if job:
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    service.cancel(principal.id, operation['id'])
                    raise
            return ToolOutcome({'operation': service.operation(principal.id, operation['id'])})
        raise ValueError('Unknown character tool')
    except (KeyError, ValueError, PermissionError, HTTPException):
        return ToolOutcome({'refused': True, 'error': 'Character arguments, owned artwork, or task authority are unavailable. Inspect Characters before retrying.'})


def schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def register(registry):
    text = {'type': 'string'}
    clip = {'type': 'string', 'enum': ['idle', 'thinking', 'working', 'waiting', 'success', 'error', 'paused',
                                      'sleeping', 'walk', 'hop', 'fall', 'sit', 'drag', 'wave']}
    specs = [
        ('companion_act', 'Request a brief expressive action for this agent’s pet; important task states take priority.',
         schema({'name': {'type': 'string', 'enum': ['wave', 'sit', 'hop', 'sleep', 'home', 'approach']},
                 'seconds': {'type': 'number', 'minimum': .1, 'maximum': 15}, 'target': text}, ['name']), 'write', 'never'),
        ('character_validate', 'Validate an owned character pack and inspect available animation clips.',
         schema({'pack_id': text, 'revision': {'type': 'integer', 'minimum': 1}}, ['pack_id']), 'read', 'never'),
        ('character_build', 'Compile an owned sprite-sheet artifact into a character draft. The user reviews and accepts it in Characters.',
         schema({'artifact_id': text, 'name': text, 'pack_id': text, 'clip': clip,
                 'columns': {'type': 'integer', 'minimum': 1, 'maximum': 16},
                 'rows': {'type': 'integer', 'minimum': 1, 'maximum': 16},
                 'duration_ms': {'type': 'integer', 'minimum': 20, 'maximum': 5000},
                 'remove_background': {'type': 'boolean'}}, ['artifact_id', 'name']), 'write', 'never'),
        ('character_generate', 'Create a canonical character reference or regenerate a clip using a configured image account. Requires exact billing approval.',
         schema({'provider_id': text, 'model': text, 'prompt': text, 'name': text,
                 'phase': {'type': 'string', 'enum': ['reference', 'clip']}, 'reference_id': text,
                 'pack_id': text, 'clip': clip, 'remove_background': {'type': 'boolean'}}, ['provider_id', 'model', 'prompt', 'name']), 'external', 'always'),
    ]
    for name, description, parameters, mutability, approval in specs:
        registry.register(ToolSpec(name, description, parameters, mutability, False, approval,
                                   execute, decide, expose_read_only=name == 'character_validate'))
