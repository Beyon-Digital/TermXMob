"""Owner-scoped character revisions and authoritative companion presentation.

Desktop geometry and credentials never enter this service's presentation data.
Native clients consume snapshots; animation and physics run on the local host.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event, RLock
from time import time
from uuid import uuid4

from fastapi import HTTPException

from .packs import compile_sheet, export_pack, import_pack, starter_pack, validate_pack

DEFAULTS = {'enabled': False, 'size': 96, 'activity': 'calm', 'reduced_motion': False,
            'pinned': [], 'hidden': [], 'assignments': {}}
STATES = {'queued': 'waiting', 'planning': 'thinking', 'running': 'thinking',
          'awaiting_approval': 'waiting', 'recovery_confirmation_required': 'waiting',
          'recovering': 'waiting', 'paused': 'paused', 'cancelling': 'waiting',
          'cancelled': 'idle', 'completed': 'success', 'failed': 'error'}
ACTIONS = frozenset({'wave', 'sit', 'hop', 'sleep', 'home', 'approach'})
TOOL_ITEM_TYPES = frozenset({'commandExecution', 'fileChange', 'mcpToolCall', 'webSearch',
                             'dynamicToolCall', 'collabToolCall', 'collabAgentToolCall', 'imageView'})


class CharacterService:
    def __init__(self, state, directory: Path):
        self.state = state
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = RLock()
        self.db = sqlite3.connect(directory / 'characters.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE IF NOT EXISTS packs(owner TEXT,id TEXT,name TEXT,head INTEGER,
                accepted INTEGER,reference_id TEXT,PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS revisions(owner TEXT,id TEXT,revision INTEGER,
                manifest TEXT,archive BLOB,PRIMARY KEY(owner,id,revision));
            CREATE TABLE IF NOT EXISTS operations(owner TEXT,id TEXT,digest TEXT,body TEXT,
                PRIMARY KEY(owner,id));
        ''')
        # An interrupted charge is reconciled by MediaProvider, never replayed.
        for row in self.db.execute('SELECT owner,id,body FROM operations').fetchall():
            op = json.loads(row['body'])
            if op['status'] in {'queued', 'running'}:
                op.update(status='unknown', error='Creation was interrupted. Inspect provider usage before starting another operation.')
                self.db.execute('UPDATE operations SET body=? WHERE owner=? AND id=?',
                                (json.dumps(op), row['owner'], row['id']))
        self.db.commit()
        (directory / 'characters.sqlite3').chmod(0o600)
        self.starter = starter_pack()
        self.activities: dict[str, str] = {}
        self._active_tools: dict[str, set[str]] = {}
        self.actions: dict[str, dict] = {}
        self._jobs: set[asyncio.Task] = set()
        self._job_index: dict[tuple[str, str], asyncio.Task] = {}
        self._cancellations: dict[tuple[str, str], Event] = {}
        self._closed = False
        state.agent._listeners.add(self.on_event)

    def preferences(self, owner):
        with self.lock:
            row = self.db.execute('SELECT body FROM preferences WHERE owner=?', (owner,)).fetchone()
        return {**deepcopy(DEFAULTS), **(json.loads(row['body']) if row else {})}

    def update_preferences(self, owner, changes):
        if set(changes) - set(DEFAULTS):
            raise ValueError('Unknown companion preference')
        for key in ('enabled', 'reduced_motion'):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError('Use a boolean companion preference')
        if 'size' in changes and (type(changes['size']) not in {int, float} or not 48 <= changes['size'] <= 192):
            raise ValueError('Character size must be between 48 and 192')
        if 'activity' in changes and changes['activity'] not in {'calm', 'playful'}:
            raise ValueError('Choose calm or playful activity')
        for key in ('pinned', 'hidden'):
            if key in changes and (not isinstance(changes[key], list) or len(changes[key]) > 500 or
                                   any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in changes[key])):
                raise ValueError('Choose a bounded list of companion identities')
        if 'assignments' in changes:
            assignments = changes['assignments']
            if not isinstance(assignments, dict) or len(assignments) > 500:
                raise ValueError('Too many character assignments')
            for instance, pack in assignments.items():
                if not isinstance(instance, str) or not 1 <= len(instance) <= 200 or not isinstance(pack, str):
                    raise ValueError('Invalid character assignment')
                if pack != self.starter[0]['id']:
                    metadata = self.metadata(owner, pack)
                    if not metadata['accepted_revision']:
                        raise ValueError('Review and accept the character before assigning it')
        with self.lock:
            result = {**self.preferences(owner), **changes}
            self.db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)', (owner, json.dumps(result)))
            self.db.commit()
        return result

    def metadata(self, owner, identifier):
        if identifier == self.starter[0]['id']:
            return {'id': identifier, 'name': self.starter[0]['name'], 'revision': 1,
                    'builtin': True, 'accepted': True, 'accepted_revision': 1, 'fallback_clips': []}
        with self.lock:
            row = self.db.execute('SELECT * FROM packs WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if not row:
                raise KeyError(identifier)
            manifest = self.db.execute('SELECT manifest FROM revisions WHERE owner=? AND id=? AND revision=?',
                                       (owner, identifier, row['head'])).fetchone()
        return {'id': identifier, 'name': row['name'], 'revision': row['head'], 'builtin': False,
                'accepted': row['head'] == row['accepted'], 'accepted_revision': row['accepted'],
                'reference_id': row['reference_id'], 'fallback_clips': json.loads(manifest[0]).get('fallback_clips', [])}

    def library(self, owner):
        with self.lock:
            ids = [row[0] for row in self.db.execute('SELECT id FROM packs WHERE owner=? ORDER BY name,id', (owner,))]
            operations = [json.loads(row[0]) for row in self.db.execute(
                'SELECT body FROM operations WHERE owner=? ORDER BY rowid DESC LIMIT 40', (owner,))]
        return {'packs': [self.metadata(owner, self.starter[0]['id']), *[self.metadata(owner, x) for x in ids]],
                'preferences': self.preferences(owner), 'operations': operations}

    def pack(self, owner, identifier, revision=None):
        if identifier == self.starter[0]['id']:
            if revision not in {None, 1}:
                raise KeyError(identifier)
            return deepcopy(self.starter[0]), self.starter[1].copy()
        meta = self.metadata(owner, identifier)
        with self.lock:
            row = self.db.execute('SELECT archive FROM revisions WHERE owner=? AND id=? AND revision=?',
                                  (owner, identifier, revision or meta['revision'])).fetchone()
        if not row:
            raise KeyError(identifier)
        return import_pack(bytes(row[0]))

    def save_pack(self, owner, manifest, assets, *, identifier=None, reference_id=None, authority=lambda: None):
        # Imported IDs never overwrite an existing local/built-in character.
        identifier = identifier or 'character-' + uuid4().hex
        if identifier == self.starter[0]['id']:
            raise ValueError('Duplicate the built-in character before editing it')
        with self.lock:
            row = self.db.execute('SELECT head FROM packs WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            revision = row[0] + 1 if row else 1
            manifest = {**manifest, 'id': identifier, 'revision': revision}
            manifest = validate_pack(manifest, assets)
            archive = export_pack(manifest, assets)
            authority()
            self.db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',
                            (owner, identifier, revision, json.dumps(manifest), archive))
            if row:
                self.db.execute('UPDATE packs SET name=?,head=?,reference_id=COALESCE(?,reference_id) WHERE owner=? AND id=?',
                                (manifest['name'], revision, reference_id, owner, identifier))
            else:
                self.db.execute('INSERT INTO packs VALUES(?,?,?,?,NULL,?)',
                                (owner, identifier, manifest['name'], revision, reference_id))
            self.db.commit()
        return self.metadata(owner, identifier)

    def accept(self, owner, identifier, revision):
        metadata = self.metadata(owner, identifier)
        if metadata['revision'] != revision:
            raise HTTPException(409, 'Character changed. Review the latest revision before accepting it.')
        if not metadata['builtin']:
            with self.lock:
                self.db.execute('UPDATE packs SET accepted=? WHERE owner=? AND id=? AND head=?',
                                (revision, owner, identifier, revision))
                self.db.commit()
        return self.metadata(owner, identifier)

    def build(self, owner, *, artifact_id, name, pack_id=None, clip='idle', columns=4, rows=2,
              duration_ms=140, reference_id=None, remove_background=False, authority=lambda: None):
        source = self.state.artifacts.get(owner, artifact_id)
        if not source['mime'].startswith('image/'):
            raise ValueError('Build animation frames from an image artifact')
        raw, _ = self.state.artifacts.binary(owner, artifact_id)
        base = self.pack(owner, pack_id) if pack_id else None
        manifest, assets = compile_sheet(raw, name=name, pack_id=pack_id or 'character-' + uuid4().hex, clip=clip,
                                        columns=columns, rows=rows, duration_ms=duration_ms, base=base,
                                        remove_background=remove_background)
        authority()
        return self.save_pack(owner, manifest, assets, identifier=pack_id, reference_id=reference_id, authority=authority)

    def edit_clip(self, owner, identifier, clip, duration_ms, foot_anchor=None):
        manifest, assets = self.pack(owner, identifier)
        if clip not in manifest['clips'] or type(duration_ms) is not int or not 20 <= duration_ms <= 5000:
            raise ValueError('Choose a valid clip and frame duration (20–5000 ms)')
        for frame in manifest['clips'][clip]['frames']:
            frame['duration_ms'] = duration_ms
        if foot_anchor is not None:
            manifest['foot_anchor'] = foot_anchor
        return self.save_pack(owner, manifest, assets, identifier=identifier)

    def replace_frame(self, owner, identifier, index, clip, artifact_id, *, authority=lambda: None):
        """Align one transparent image to the pack baseline without touching other poses."""
        from io import BytesIO
        from PIL import Image
        manifest, assets = self.pack(owner, identifier)
        animation = manifest['clips'].get(clip)
        if not animation or type(index) is not int or not 0 <= index < len(animation['frames']):
            raise ValueError('Choose an existing animation frame')
        source = self.state.artifacts.get(owner, artifact_id)
        if source['mime'] != 'image/png':
            raise ValueError('Replace a frame with a transparent PNG')
        raw, _ = self.state.artifacts.binary(owner, artifact_id)
        with Image.open(BytesIO(raw)) as image:
            if image.width > 4096 or image.height > 4096 or image.width * image.height > 16 * 1024 * 1024:
                raise ValueError('Replacement image exceeds the character image limit')
            image = image.convert('RGBA')
        alpha = image.getchannel('A')
        bounds = alpha.getbbox()
        if not bounds or alpha.getextrema()[0] != 0:
            raise ValueError('Use a visible character with a transparent background')
        image = image.crop(bounds)
        width, height = manifest['logical_size']['width'], manifest['logical_size']['height']
        anchor = manifest['foot_anchor']
        available_width = min(anchor['x'], width - anchor['x']) * 2
        available_height = anchor['y']
        if available_width < 1 or available_height < 1:
            raise ValueError('Move the foot anchor inside the character canvas before replacing frames')
        ratio = min(available_width / image.width, available_height / image.height)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (width, height))
        canvas.alpha_composite(image, (round(anchor['x'] - image.width / 2), round(anchor['y'] - image.height)))
        output = BytesIO()
        canvas.save(output, 'PNG')
        filename = 'frame-' + uuid4().hex + '.png'
        assets[filename] = output.getvalue()
        atlas_index = len(manifest['atlases'])
        manifest['atlases'].append({'file': filename, 'width': width, 'height': height})
        animation['frames'][index] = {**animation['frames'][index], 'atlas': atlas_index, 'x': 0, 'y': 0,
                                      'width': width, 'height': height}
        used = sorted({frame['atlas'] for value in manifest['clips'].values() for frame in value['frames']})
        mapping = {old: new for new, old in enumerate(used)}
        manifest['atlases'] = [manifest['atlases'][old] for old in used]
        for value in manifest['clips'].values():
            for frame in value['frames']:
                frame['atlas'] = mapping[frame['atlas']]
        assets = {atlas['file']: assets[atlas['file']] for atlas in manifest['atlases']}
        return self.save_pack(owner, manifest, assets, identifier=identifier, authority=authority)

    def on_event(self, task_id, event):
        kind = event['type']
        payload = event.get('payload') or {}
        item = payload.get('item') or {}
        # Native engines use different envelopes for the same tool lifecycle.
        # Message/reasoning items must not become unfinished Codex tools.
        tool_event = kind in {'tool.started', 'tool.finished', 'engine.tool.started',
                              'engine.tool.progress', 'engine.tool.completed',
                              'engine.tool.failed', 'engine.tool.cancelled'} or (
            kind in {'engine.item.started', 'engine.item.completed'} and item.get('type') in TOOL_ITEM_TYPES)
        if tool_event:
            call_id = ((payload.get('call') or {}).get('call_id') or payload.get('call_id') or
                       payload.get('tool_call_id') or payload.get('toolCallId') or
                       payload.get('itemId') or item.get('id') or 'legacy')
            status = payload.get('status') or item.get('status')
            finished = kind in {'tool.finished', 'engine.tool.completed', 'engine.tool.failed',
                                'engine.tool.cancelled', 'engine.item.completed'} or status in {
                'completed', 'failed', 'error', 'cancelled', 'canceled', 'interrupted', 'declined', 'denied'}
            pending = self._active_tools.get(task_id, set())
            if finished:
                pending.discard(call_id)
                if not pending:
                    self._active_tools.pop(task_id, None)
            else:
                pending = self._active_tools.setdefault(task_id, set())
                pending.add(call_id)
            self.activities[task_id] = 'working' if pending else 'thinking'
        elif kind in {'assistant.delta', 'engine.message.delta', 'engine.reasoning.delta', 'task.status'}:
            self.activities[task_id] = 'working' if self._active_tools.get(task_id) else 'thinking'
        elif kind in {'task.completed', 'task.failed', 'task.cancelled'}:
            self.activities.pop(task_id, None)
            self._active_tools.pop(task_id, None)
            self.actions.pop(task_id, None)
        # Presentation caches are bounded even with long-running hosts.
        if len(self.activities) > 2000:
            expired = next(iter(self.activities))
            self.activities.pop(expired)
            self._active_tools.pop(expired, None)

    def snapshot(self, principal):
        preferences = self.preferences(principal.id)
        pets = []
        now = time()
        enrolled_tasks = {}
        for enrolled in self.state.workspace.store.list('task', principal.id):
            if enrolled.get('parent_id'):
                enrolled_tasks.setdefault(enrolled.get('conversation_id'), []).append(enrolled)
        for record in self.state.workspace.store.list('conversation', principal.id):
            try:
                conversation = self.state.workspace.session(principal, record['id'], turns=False)
            except (KeyError, PermissionError):
                continue
            if conversation.get('archived'):
                continue
            turns = self.state.agent_store.workspace_turns_page(record['id'], descending=True, limit=1)
            tasks = [turn for turn in turns if turn.get('task_id')]
            if not tasks:
                continue
            task = self.state.agent_store.get_task(tasks[0]['task_id'])
            if not task:
                continue
            pets.append(self._pet(preferences, record['id'], record['id'], conversation.get('title') or 'Agent', task, now))
            for enrolled in enrolled_tasks.get(record['id'], []):
                try:
                    self.state.workspace.record(principal, 'task', enrolled['id'])
                except (KeyError, PermissionError):
                    continue
                child = self.state.agent_store.get_task(enrolled['id'])
                if child and child['status'] not in {'completed', 'cancelled'}:
                    pets.append(self._pet(preferences, child['id'], record['id'], 'Subagent', child, now))
        hidden = set(preferences['hidden'])
        pets = [pet for pet in pets if pet['id'] not in hidden]
        pinned = {identifier: index for index, identifier in enumerate(preferences['pinned'])}
        pets.sort(key=lambda pet: (0 if pet['id'] in pinned else 1,
                                  pinned.get(pet['id'], 999),
                                  0 if pet['state'] in {'waiting', 'error'} else 1, -pet['updated_at'], pet['id']))
        for index, pet in enumerate(pets):
            pack_id = preferences['assignments'].get(pet['id'], self.starter[0]['id'])
            try:
                meta = self.metadata(principal.id, pack_id)
                if not meta['accepted_revision']:
                    raise KeyError(pack_id)
            except KeyError:
                meta = self.metadata(principal.id, self.starter[0]['id'])
            pet.update(pack_id=meta['id'], pack_revision=meta['accepted_revision'],
                       roaming=preferences['enabled'] and index < 4)
        return {'enabled': preferences['enabled'], 'preferences': preferences, 'pets': pets}

    def _pet(self, preferences, identifier, session_id, name, task, now):
        status = task['status']
        state = STATES.get(status, 'idle')
        if status == 'running':
            state = self.activities.get(task['id'], 'thinking')
        updated = task.get('updated_at', now)
        # Terminal celebrations expire by authoritative event time, including reconnect.
        if status in {'completed', 'cancelled'} and now - updated > 5:
            state = 'idle'
        action = self.actions.get(task['id'])
        if action and (action['expires_at'] <= now or state in {'waiting', 'error', 'paused'}):
            action = None
        return {'id': identifier, 'session_id': session_id, 'task_id': task['id'], 'name': name[:120],
                'state': state, 'updated_at': updated, 'action': action}

    def act(self, task_id, args):
        self.state.workspace.validate_task_execution(task_id)
        name = args.get('name')
        if name not in ACTIONS:
            raise ValueError('Unknown companion action')
        seconds = args.get('seconds', 4)
        if type(seconds) not in {int, float} or not 0 < seconds <= 15:
            raise ValueError('Companion actions last at most 15 seconds')
        target = args.get('target')
        if target is not None and (not isinstance(target, str) or len(target) > 200):
            raise ValueError('Use a local surface identity as the target')
        action = {'id': uuid4().hex, 'name': name, 'target': target, 'expires_at': time() + seconds}
        self.actions[task_id] = action
        self.state.agent.emit_external(task_id, 'companion.action', action)
        return action

    def operation(self, owner, identifier):
        with self.lock:
            row = self.db.execute('SELECT body FROM operations WHERE owner=? AND id=?', (owner, identifier)).fetchone()
        if not row:
            raise KeyError(identifier)
        return json.loads(row[0])

    def _operation(self, owner, op):
        with self.lock:
            self.db.execute('UPDATE operations SET body=? WHERE owner=? AND id=?', (json.dumps(op), owner, op['id']))
            self.db.commit()

    def generate(self, owner, args, *, authority):
        if self._closed:
            raise HTTPException(503, 'Character creation is shutting down')
        if not args.get('acknowledge_billing'):
            raise HTTPException(409, 'Confirm selected provider/model usage before generation')
        identifier = args['request_id']
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        with self.lock:
            row = self.db.execute('SELECT digest,body FROM operations WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise HTTPException(409, 'Creation request ID reused with different arguments')
                return json.loads(row['body'])
            if args.get('pack_id'):
                self.metadata(owner, args['pack_id'])
            op = {'id': identifier, 'status': 'queued', 'phase': args.get('phase', 'reference'), 'created_at': time()}
            self.db.execute('INSERT INTO operations VALUES(?,?,?,?)', (owner, identifier, digest, json.dumps(op)))
            self.db.commit()
        cancelled = Event()
        self._cancellations[(owner, identifier)] = cancelled
        def guarded_authority():
            if cancelled.is_set() or self._closed:
                raise PermissionError('Character operation cancelled')
            authority()
        job = asyncio.create_task(self._generate(owner, args.copy(), op.copy(), guarded_authority))
        self._jobs.add(job)
        self._job_index[(owner, identifier)] = job
        job.add_done_callback(self._jobs.discard)
        job.add_done_callback(lambda _: self._job_index.pop((owner, identifier), None))
        job.add_done_callback(lambda _: self._cancellations.pop((owner, identifier), None))
        return op

    def cancel(self, owner, identifier):
        op = self.operation(owner, identifier)
        job = self._job_index.get((owner, identifier))
        if job:
            self._cancellations[(owner, identifier)].set()
            job.cancel()
            op.update(status='unknown', error='Creation cancelled. Provider charges may already have occurred; this request will not be replayed.')
            self._operation(owner, op)
        return op

    async def _generate(self, owner, args, op, authority):
        try:
            authority()
            op['status'] = 'running'
            self._operation(owner, op)
            phase = args.get('phase', 'reference')
            reference = args.get('reference_id')
            if not reference and args.get('pack_id'):
                reference = self.metadata(owner, args['pack_id']).get('reference_id')
            if phase == 'clip' and not reference:
                raise ValueError('Create or select a canonical character reference first')
            if reference:
                source = self.state.artifacts.get(owner, reference)
                if source.get('project') != args.get('project_id'):
                    raise PermissionError('Reference belongs to another project')
            clip = args.get('clip', 'idle')
            prompt = ('Create an original small illustrated desktop companion, full body, centered, consistent proportions, '
                      'transparent background, no text, no frame border. Character description: ' + args['prompt'])
            if phase == 'clip':
                prompt = (f'Use the attached canonical character reference exactly. Create a sprite sheet for {clip}: '
                          '4 columns and 2 rows, exactly 8 evenly spaced equal-size cells in row-major time order, '
                          'full character in every cell, identical scale, transparent background, no labels or borders. '
                          'Keep a shared ground baseline and consistent character identity. ' + args['prompt'])
            media_args = {key: args[key] for key in ('provider_id', 'model', 'acknowledge_billing')}
            media_args.update(request_id='character-' + op['id'], operation='image-edit' if phase == 'clip' else 'image-generate',
                              prompt=prompt, input_id=reference)
            if args['model'].startswith('gpt-image'):
                media_args['background'] = 'transparent'
            artifact = await self.state.media.generate(owner, args.get('project_id'), media_args)
            authority()
            op['artifact_id'] = artifact['id']
            if phase == 'clip':
                pack = await asyncio.to_thread(self.build, owner, artifact_id=artifact['id'], name=args['name'],
                                               pack_id=args.get('pack_id'), clip=clip, reference_id=reference,
                                               remove_background=args.get('remove_background', False), authority=authority)
                op.update(pack_id=pack['id'], phase='preview_ready')
            else:
                op['phase'] = 'reference_ready'
            op['status'] = 'completed'
        except asyncio.CancelledError:
            op.update(status='unknown', error='Creation interrupted. Inspect provider usage before retrying.')
            raise
        except (ValueError, KeyError) as exc:
            op.update(status='failed', error=str(exc)[:240])
        except PermissionError:
            op.update(status='failed', error='Character creation authority is no longer available.')
        except HTTPException as exc:
            op.update(status='unknown', error=str(exc.detail)[:240])
        except Exception:
            op.update(status='unknown', error='Provider outcome requires inspection. This request will not be replayed.')
        finally:
            self._operation(owner, op)

    async def close(self):
        self._closed = True
        self.state.agent._listeners.discard(self.on_event)
        for cancellation in self._cancellations.values():
            cancellation.set()
        jobs = list(self._jobs)
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        with self.lock:
            self.db.close()
