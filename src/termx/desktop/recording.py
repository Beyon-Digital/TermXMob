"""Consented, expiring window recordings and tested immutable skill drafts."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import secrets
import threading
import weakref
from time import time

from termx.agent.policy import redact
from termx.desktop.windows import WindowCapture

_PRIVATE = {}
_LOCK = threading.RLock()
_PRIVACY_REVISION = 0
_PRIVACY_LISTENERS = []

def on_capture_private(callback):
    """Weak host observers close their transports before private acknowledgement."""
    with _LOCK:
        _PRIVACY_LISTENERS.append(weakref.WeakMethod(callback))


def set_capture_private(identifier, *, expires_at, valid):
    """Shared host observation barrier, never an execution grant."""
    global _PRIVACY_REVISION
    with _LOCK:
        _PRIVATE[identifier]=(expires_at,valid)
        _PRIVACY_REVISION+=1
        callbacks=[callback() for callback in _PRIVACY_LISTENERS if callback() is not None]
        _PRIVACY_LISTENERS[:]=[callback for callback in _PRIVACY_LISTENERS if callback() is not None]
    # Never hold the privacy mutex while closing peer transports: their
    # capture workers may be checking this same epoch during cancellation.
    failures=[]
    for callback in callbacks:
        try:callback()
        except Exception as exc:failures.append(exc)
    if failures:raise PermissionError('Private barrier retained; remote observation shutdown could not be confirmed') from failures[0]

def clear_capture_private(identifier):
    global _PRIVACY_REVISION
    with _LOCK:
        if identifier in _PRIVATE:
            _PRIVATE.pop(identifier,None)
            _PRIVACY_REVISION+=1

def assert_agent_capture_allowed():
    global _PRIVACY_REVISION
    with _LOCK:
        for identifier, (expires, valid) in list(_PRIVATE.items()):
            if expires <= time() or not valid():
                _PRIVATE.pop(identifier, None)
                _PRIVACY_REVISION+=1
        if _PRIVATE: raise PermissionError('Computer observation is paused while a private browser or window capture session is active')


def capture_privacy_revision():
    with _LOCK:
        assert_agent_capture_allowed()
        return _PRIVACY_REVISION


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class WindowRecordingService:
    def __init__(self, records, *, port=None, session_valid=None):
        global _PRIVACY_REVISION
        self.records = records
        self.port = port or WindowCapture()
        self.session_valid = session_valid or (lambda principal, session, policy: False)
        for row in records.list('window-capture'):
            with _LOCK:
                if row['id'] in _PRIVATE: _PRIVACY_REVISION += 1
                _PRIVATE.pop(row['id'], None)
            # A crashed client may still display its last private preview.
            # Reconstruct observation suppression without reviving consent.
            row.update(stopped=True, recording=False)
            records.put('window-capture', row['id'], row)
            if row['private']: self._retain_private(row['id'])

    def _retain_private(self, identifier):
        def current():
            row = self.records.get('window-capture', identifier)
            return bool(row and row['private'])
        set_capture_private(identifier, expires_at=float('inf'), valid=current)

    def _expire(self, row):
        if not row['stopped'] or row['recording']:
            row.update(stopped=True, recording=False, revision=row['revision'] + 1)
            self.records.put('window-capture', row['id'], row)
        # Expiry removes execution authority, never consent to observe pixels
        # which an independent human window may still retain.
        if row['private']: self._retain_private(row['id'])

    def close(self):
        for row in self.records.list('window-capture'):
            if not row['stopped'] or row['private']: self.stop(row['id'], row['principal_id'])

    def get(self, identifier, principal):
        row = self.records.get('window-capture', identifier)
        if not row or row['principal_id'] != principal: raise KeyError(identifier)
        if row['stopped'] or row['expires_at'] <= time() or not self.session_valid(principal, row['session_id'], row['policy_version']):
            self._expire(row)
            raise PermissionError('Capture consent expired or was revoked; start a new session')
        return row

    def start(self, principal, session, policy, window_id, expires_in=600):
        if not 30 <= expires_in <= 1800: raise ValueError('Capture consent must expire within 30 minutes')
        target = next((row for row in self.port.list() if row['id'] == window_id), None)
        if not target: raise ValueError('Select a current window')
        identifier = secrets.token_urlsafe(16)
        return self.records.put('window-capture', identifier, {'id': identifier, 'principal_id': principal, 'session_id': session, 'policy_version': policy,
            'window': target, 'private': False, 'recording': False, 'stopped': False, 'revision': 1, 'redactions': [], 'steps': [], 'expires_at': time() + expires_in})

    def configure(self, identifier, principal, *, private=None, recording=None, redactions=None):
        global _PRIVACY_REVISION
        row = self.get(identifier, principal)
        if private is not None:
            row['private'] = bool(private)
            if private: row['recording'] = False
            # Persist the private flag before registering its observation
            # barrier so concurrent Computer workers cannot prune it.
            self.records.put('window-capture', identifier, row)
            if private: self._retain_private(identifier)
            else: clear_capture_private(identifier)
            if private:
                for review in self.records.list('review'):
                    if review['principal_id'] == principal and review.get('effect') == 'unknown' and review['status'] in {'needs_user', 'approved_once', 'permitted'}:
                        review.update(status='invalidated', reason='Private window capture invalidated queued desktop/process control')
                        self.records.put('review', review['id'], review)
        if recording is not None:
            if row['private'] and recording: raise PermissionError('Recording is disabled in private mode')
            row['recording'] = bool(recording)
        if redactions is not None:
            if len(redactions) > 30: raise ValueError('At most 30 redaction regions')
            for rectangle in redactions:
                if set(rectangle) != {'x', 'y', 'width', 'height'} or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not 0 <= v <= 1 for v in rectangle.values()) or rectangle['x'] + rectangle['width'] > 1 or rectangle['y'] + rectangle['height'] > 1: raise ValueError('Redactions must be normalized rectangles inside the selected window')
            row['redactions'] = redactions
        row['revision'] += 1
        return self.records.put('window-capture', identifier, row)

    def stop(self, identifier, principal):
        global _PRIVACY_REVISION
        row = self.records.get('window-capture', identifier)
        if not row or row['principal_id'] != principal: raise KeyError(identifier)
        row.update(stopped=True, recording=False, private=False, revision=row['revision'] + 1)
        with _LOCK:
            if identifier in _PRIVATE: _PRIVACY_REVISION += 1
            _PRIVATE.pop(identifier, None)
        return self.records.put('window-capture', identifier, row)

    async def frame(self, identifier, principal, *, human=False):
        row = self.get(identifier, principal); revision = row['revision']
        if row['private'] and not human: raise PermissionError('Private capture is visible only to its human owner')
        data = await asyncio.to_thread(self.port.capture, row['window'])
        current = self.get(identifier, principal)
        if current['revision'] != revision or (current['private'] and not human): raise PermissionError('Capture scope changed while the frame was in flight')
        if len(data) > 20_000_000: raise ValueError('Window frame exceeds 20 MB')
        from PIL import Image, ImageDraw
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 40_000_000: raise ValueError('Window frame exceeds 40 megapixels')
        image.load(); image = image.convert('RGB'); draw = ImageDraw.Draw(image)
        for region in row['redactions']:
            x, y = region['x'] * image.width, region['y'] * image.height
            draw.rectangle((x, y, x + region['width'] * image.width, y + region['height'] * image.height), fill='black')
        output = io.BytesIO(); image.save(output, 'JPEG', quality=80)
        return output.getvalue()

    @staticmethod
    def event(event, *, parameter=None):
        kind = event.get('type')
        if kind == 'pointer':
            if set(event) - {'type', 'action', 'x', 'y', 'button', 'down'} or event.get('action') not in {'click', 'move', 'down', 'up'} or event.get('button', 1) not in {1, 2, 3}: raise ValueError('Unsupported pointer event')
            if any(not isinstance(event.get(key), (int, float)) or not 0 <= event[key] <= 1 for key in ('x', 'y')): raise ValueError('Pointer must remain inside the selected window')
        elif kind == 'text':
            if set(event) - {'type', 'data'} or not isinstance(event.get('data'), str) or len(event['data']) > 2000: raise ValueError('Text must contain at most 2000 characters')
            if parameter and not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', parameter): raise ValueError('Use a safe parameter name')
        elif kind == 'key':
            if set(event) - {'type', 'key', 'action'} or event.get('key') not in {'Tab', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape', 'Backspace', 'Enter'} or event.get('action') not in {'down', 'up'}: raise ValueError('Only navigation keys may be recorded')
        else: raise ValueError('Unsupported window input')
        return dict(event)

    async def input(self, identifier, principal, event, *, parameter=None):
        row = self.get(identifier, principal); revision = row['revision']
        event = self.event(event, parameter=parameter)
        await asyncio.to_thread(self.port.input, row['window'], event)
        current = self.get(identifier, principal)
        if current['revision'] != revision: raise PermissionError('Capture scope changed; input outcome cannot be recorded')
        if row['recording'] and not row['private'] and len(row['steps']) < 200:
            if event['type'] != 'text': row['steps'].append({'event': event})
            elif parameter: row['steps'].append({'event': {'type': 'text', 'data': '${' + parameter + '}'}, 'parameter': parameter})
            # Plain text and credentials are never saved, even when recording.
            self.records.put('window-capture', identifier, row)
        return {'ok': True, 'recorded': bool(row['recording'] and (event['type'] != 'text' or parameter))}

    def draft(self, identifier, principal, name):
        row = self.get(identifier, principal)
        if row['private'] or not row['steps']: raise ValueError('Stop private mode and record an action before creating a draft')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,59}', name): raise ValueError('Use a lower-case skill name')
        parameters = sorted({step['parameter'] for step in row['steps'] if step.get('parameter')})
        content = '---\nname: ' + name + '\ndescription: "Recorded window workflow; requires fresh scoped human consent"\n---\n\n# ' + name + '\n\nSelect the intended application window and obtain a fresh expiring control grant.\nReview the target and each step before execution. Private mode stops all recording.\n\nParameters: ' + ', '.join('${' + key + '}' for key in parameters) + '\n\n```json\n' + json.dumps(row['steps'], indent=2) + '\n```\n'
        draft = {'id': secrets.token_urlsafe(16), 'principal_id': principal, 'source': 'consented-window-recording', 'source_capture_id': identifier,
                 'name': name, 'content': content, 'steps': row['steps'], 'parameters': parameters, 'revision': 1, 'test': None, 'created_at': time()}
        return self.records.put('window-skill', draft['id'], draft)

    def get_draft(self, identifier, principal):
        row = self.records.get('window-skill', identifier)
        if not row or row['principal_id'] != principal: raise KeyError(identifier)
        return row

    def edit(self, identifier, principal, content):
        row = self.get_draft(identifier, principal)
        if not content.strip() or len(content) > 100_000 or redact(content) != content: raise ValueError('Skill text must be nonempty and contain no embedded credentials')
        blocks = re.findall(r'```json\s*\n(.*?)\n```', content, re.S)
        if len(blocks) != 1: raise ValueError('Keep exactly one editable JSON step list in the draft')
        steps = json.loads(blocks[0])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 200: raise ValueError('Draft must contain one to 200 steps')
        for step in steps:
            if not isinstance(step, dict) or set(step) - {'event', 'parameter'}: raise ValueError('Invalid recorded step')
            self.event(step.get('event', {}), parameter=step.get('parameter'))
            if step['event']['type'] == 'text' and (not step.get('parameter') or step['event']['data'] != '${' + step['parameter'] + '}'): raise ValueError('Recorded text must be a named parameter, with no embedded value')
        row.update(content=content, steps=steps, parameters=sorted({step['parameter'] for step in steps if step.get('parameter')}), revision=row['revision'] + 1, test=None)
        return self.records.put('window-skill', identifier, row)

    async def test(self, identifier, principal, target_capture_id, parameters, safe_target):
        draft = self.get_draft(identifier, principal); target = self.get(target_capture_id, principal)
        if not safe_target: raise ValueError('Explicitly authorize replay against a safe test window')
        source = self.records.get('window-capture', draft['source_capture_id'])
        if source and source['window']['id'] == target['window']['id']: raise ValueError('Select a separate safe test window rather than replaying against the recorded source')
        if target['private'] or target['recording']: raise PermissionError('Test target must be public and not recording')
        if set(parameters) != set(draft['parameters']) or any(not isinstance(value, str) or len(value) > 2000 for value in parameters.values()): raise ValueError('Provide exactly the draft parameters')
        revision = draft['revision']; target_revision = target['revision']
        await self.frame(target_capture_id, principal, human=True)
        for step in draft['steps']:
            current = self.get(target_capture_id, principal)
            if current['revision'] != target_revision or self.get_draft(identifier, principal)['revision'] != revision: raise PermissionError('Draft or test consent changed during replay')
            event = dict(step['event'])
            if step.get('parameter'): event['data'] = parameters[step['parameter']]
            await self.input(target_capture_id, principal, event)
            await asyncio.sleep(0.08)
        await self.frame(target_capture_id, principal, human=True)
        # No screenshots, parameter values or window titles enter the evidence.
        draft = self.get_draft(identifier, principal)
        if draft['revision'] != revision: raise PermissionError('Draft changed during test')
        draft['test'] = {'digest': digest({'content': draft['content'], 'steps': draft['steps']}), 'passed_at': time(), 'steps_executed': len(draft['steps']), 'target_capture_id': target_capture_id, 'expires_at': time() + 600}
        return self.records.put('window-skill', identifier, draft)

    def publish(self, identifier, principal_id, principal, extensions, version):
        draft = self.get_draft(identifier, principal_id); evidence = draft.get('test')
        if not evidence or evidence['expires_at'] <= time() or evidence['digest'] != digest({'content': draft['content'], 'steps': draft['steps']}): raise PermissionError('Test the current edited draft against a safe target before activation')
        self.get(evidence['target_capture_id'], principal_id)
        preview = extensions.preview(principal, {'id': draft['name'], 'version': version, 'markdown': draft['content'], 'permissions': ['agent-run']}, 'skill-markdown/v1')
        installed = extensions.install(principal, preview['id'], preview['digest'])
        draft.update(published_version=version, published_digest=installed['active_digest'])
        self.records.put('window-skill', identifier, draft)
        return installed
