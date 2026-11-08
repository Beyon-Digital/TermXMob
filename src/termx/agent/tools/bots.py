"""Main-agent tools for managing the owner's bots.

Every call goes through ``OrchestratorService.command``, which proves the caller is the owner's own
unscheduled main-agent turn and keeps a ``call_id`` receipt so a repeated call never repeats the effect.
A bot's task, a sub-agent or a scheduled run cannot use these tools. Creating, changing or
assigning work to a bot always asks the owner, and Auto never answers for them.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException

from termx.agent.policy import PolicyDecision
from termx.agent.providers import ProviderCall
from termx.agent.tools.registry import ToolContext, ToolOutcome, ToolRegistry, ToolSpec, decide_never
from termx.workspace.bots import READ_TOOLS, TEMPLATES

_BOT_ID = {'type': 'string', 'description': 'A bot ID returned by list_bots.'}


async def _bots(call: ProviderCall, ctx: ToolContext) -> ToolOutcome:
    service = getattr(ctx.manager, 'orchestrator', None)
    if service is None:
        return ToolOutcome({'ok': False, 'error': 'Bots are unavailable'})
    try:
        result = await service.command(ctx.task_id, call)
    except (KeyError, ValueError, PermissionError, HTTPException) as exc:
        result = {'ok': False, 'error': 'Permission denied' if isinstance(exc, HTTPException) and exc.status_code in {403, 404} else str(exc)}
    return ToolOutcome(result, event={'name': call.name})


def ask_each_time(base, call, ctx):
    """Always asks the owner; ``delegation`` risk and no remembered answer keep Auto from approving."""
    engine = getattr(ctx, 'policy_engine', None)
    if engine is None:
        return base
    from termx.agent.policies.fingerprint import tool_key
    digest = hashlib.sha256(repr(sorted(call.arguments.items())).encode()).hexdigest()
    return engine.decide_tool(call, ctx, fingerprint=tool_key(call.name, digest), display=call.name,
                              matcher={}, base=base, risk='delegation', ask_each_time=True)


def _ask(reason, detail):
    def decide(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
        return ask_each_time(PolicyDecision(False, True, reason, detail(call, ctx)), call, ctx)
    return decide


def _create_tools(call):
    template = next((t for t in TEMPLATES if t['id'] == call.arguments.get('template')), None)
    tools = call.arguments.get('tools') or (template['tools'] if template else list(READ_TOOLS))
    return tools if isinstance(tools, list) else []


def _create_detail(call, _ctx):
    args = call.arguments
    template = next((t for t in TEMPLATES if t['id'] == args.get('template')), None)
    instructions = str(args.get('instructions') or (template['instructions'] if template else ''))
    return (f'Creates a background bot named "{str(args.get("name") or "")[:80]}". It runs as you, can use only these tools: '
            f'{", ".join(str(t) for t in _create_tools(call))}. It cannot run shell commands, use the network, publish or control the computer '
            f'until you allow it in Settings. Instructions: {instructions[:300]}')


_ask_to_create = _ask('Create a bot', _create_detail)


def _decide_create(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    tools = call.arguments.get('tools')
    if (tools is not None and not isinstance(tools, list)) or any(t not in READ_TOOLS for t in _create_tools(call)):
        return PolicyDecision(False, False, 'A bot made by the main agent is read-only',
                              'Only read-only tools are allowed (' + ', '.join(READ_TOOLS) + '). Ask the owner to widen a bot in Settings.')
    return _ask_to_create(call, ctx)


def _update_detail(call, _ctx):
    changes = [key for key in ('name', 'description', 'instructions', 'tools', 'model') if key in call.arguments]
    text = str(call.arguments.get('instructions') or '')[:300]
    return (f'Changes {", ".join(changes) or "nothing"} on bot {call.arguments.get("bot_id")}. It stays inside the bot\'s ceiling; '
            f'any schedules pause until you re-authorize them.' + (f' New instructions: {text}' if text else ''))


def _assign_detail(call, _ctx):
    return (f'Gives bot {call.arguments.get("bot_id")} a task and runs it now as you, inside its ceiling. Report goes to '
            f'{call.arguments.get("report_to") or "orchestrator"}. Task: {str(call.arguments.get("task") or "")[:300]}')


MAX_TASK = 2000      # the owner approves unattended authority for the text they are shown, so all of it must be shown
MAX_CRITERIA = 500


def _schedule_detail(call, _ctx):
    args = call.arguments
    try:
        when = json.dumps(args.get('trigger') or args.get('spec'), sort_keys=True)[:200]
    except (TypeError, ValueError):
        when = 'an invalid schedule'
    criteria = str(args.get('success_criteria') or '')
    return (f'Runs bot {args.get("bot_id")} on {when} as you, for up to {args.get("max_runs", 30)} runs over '
            f'{args.get("days", 30)} days, inside its ceiling, and reports to {args.get("report_to") or "orchestrator"}. '
            f'Anything it needs approval for waits for you. '
            f'Task: {args.get("task") or ""}' + (f' Success criteria: {criteria}' if criteria else ''))


_ask_to_schedule = _ask('Schedule a bot', _schedule_detail)


def _decide_schedule(call: ProviderCall, ctx: ToolContext) -> PolicyDecision:
    task, criteria = call.arguments.get('task'), call.arguments.get('success_criteria')
    if len(str(task or '')) > MAX_TASK or len(str(criteria or '')) > MAX_CRITERIA:
        return PolicyDecision(False, False, 'The schedule text is too long to review',
                              f'Use a shorter task (up to {MAX_TASK} characters) and success criteria (up to {MAX_CRITERIA}) '
                              'so the owner can read all of what the bot will do unattended.')
    return _ask_to_schedule(call, ctx)


def register(registry: ToolRegistry) -> None:
    def add(name, description, properties, required, *, mutability, approval, decide):
        registry.register(ToolSpec(
            name=name, description='Main agent only: ' + description,
            parameters={'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
            mutability=mutability, parallel_safe=mutability == 'read', approval=approval, execute=_bots,
            decide=decide, expose_read_only=False))

    add('list_bots', "list the owner's bots with their state, tools and whether any need the owner.", {}, [],
        mutability='read', approval='never', decide=decide_never)
    add('bot_status', "read one bot's state, instructions, tools, ceiling, peers and latest task.", {'bot_id': _BOT_ID}, ['bot_id'],
        mutability='read', approval='never', decide=decide_never)
    add('pause_bot', 'stop a bot from starting new runs. A run already in progress finishes.', {'bot_id': _BOT_ID}, ['bot_id'],
        mutability='write', approval='never', decide=decide_never)
    add('resume_bot', 'let a paused bot run again.', {'bot_id': _BOT_ID}, ['bot_id'],
        mutability='write', approval='always',
        decide=_ask('Resume a bot', lambda call, _c: f'Lets bot {call.arguments.get("bot_id")} start runs again.'))
    add('create_bot',
        "create a background bot that runs as the owner inside a ceiling. Pass a template (inbox_triager, repo_watcher, daily_report) or your own instructions. "
        "A new bot cannot run shell commands, use the network, publish or control the computer; you cannot raise its ceiling.",
        {'name': {'type': 'string'}, 'template': {'type': 'string', 'enum': [t['id'] for t in TEMPLATES]},
         'instructions': {'type': 'string', 'description': 'What the bot does each run. Required without a template.'},
         'description': {'type': 'string'}, 'project_id': {'type': 'string', 'description': 'An enrolled project from list_agent_projects. Omit for a scratch folder.'},
         'tools': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Defaults to read-only tools.'}},
        ['name'], mutability='external', approval='always', decide=_decide_create)
    add('update_bot', "change a bot's name, description, instructions, tools or model within its ceiling. It cannot change the ceiling.",
        {'bot_id': _BOT_ID, 'name': {'type': 'string'}, 'description': {'type': 'string'}, 'instructions': {'type': 'string'},
         'tools': {'type': 'array', 'items': {'type': 'string'}}, 'model': {'type': 'string'},
         'expected_revision': {'type': 'integer', 'description': 'The revision from bot_status, to refuse a stale edit.'}},
        ['bot_id'], mutability='external', approval='always', decide=_ask('Change a bot', _update_detail))
    add('delete_bot', 'delete a bot: it stops, its schedules and permissions are revoked, and its agent file is removed. Its conversation history is kept.',
        {'bot_id': _BOT_ID}, ['bot_id'], mutability='write', approval='always',
        decide=_ask('Delete a bot', lambda call, _c: f'Deletes bot {call.arguments.get("bot_id")} and revokes its schedules and permissions.'))
    add('assign_bot_task',
        'run one task on a bot now. report_to says where the result goes: orchestrator (default), owner, or bot:<id>. Refused while the bot is paused.',
        {'bot_id': _BOT_ID, 'task': {'type': 'string'}, 'report_to': {'type': 'string'}}, ['bot_id', 'task'],
        mutability='external', approval='always', decide=_ask('Give a bot a task', _assign_detail))
    add('schedule_bot',
        'schedule recurring or one-time runs of a bot, or runs started by an event. spec is one of {"kind":"interval","seconds":3600} (60 seconds to a year), '
        '{"kind":"daily","time":"09:00","timezone":"Europe/London"}, {"kind":"cron","expression":"0 9 * * 1-5","timezone":"UTC"} '
        'or {"kind":"once","at":<unix seconds>}. Instead of spec, trigger starts a run when something happens: {"kind":"message"} (the bot gets a message), '
        '{"kind":"bot_finished","bot_id":"<id>","on":"completed"} (another bot finishes), or {"kind":"file_change","glob":"src/**/*.py","debounce_s":30} '
        '(project files change). Webhook triggers are set up by the owner in Settings. report_to says where each run\'s result goes: orchestrator (default), '
        'owner, or bot:<id>. The owner approves the schedule; it stops after max_runs or days, and anything '
        'the bot needs approval for waits for the owner.',
        {'bot_id': _BOT_ID, 'task': {'type': 'string', 'description': 'What the bot does on each run.'},
         'spec': {'type': 'object', 'description': 'When to run. Omit when you pass a trigger.'},
         'trigger': {'type': 'object', 'description': 'What starts a run, instead of a time.'},
         'report_to': {'type': 'string'},
         'success_criteria': {'type': 'string', 'description': 'How to tell a run went well. Optional.'},
         'max_runs': {'type': 'integer', 'description': 'Most runs before the schedule stops. Default 30.'},
         'days': {'type': 'integer', 'description': 'Days the owner authorizes it for, 1 to 90. Default 30.'}},
        ['bot_id', 'task'], mutability='external', approval='always', decide=_decide_schedule)
