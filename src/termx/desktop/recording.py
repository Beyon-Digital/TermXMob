"""Consented, expiring window previews for a human, and the privacy barrier that pauses agent desktop control.

A capture lets one signed-in person look at, and click in, one application window, with redaction rectangles and a private
mode. Recording that window into a skill draft is retired: recorded steps were window-relative coordinates, and desktop
control reads and acts on accessibility controls by role and name, so coordinates could not be replayed or understood by it.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import secrets
import threading
import weakref
from time import time

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
        if _PRIVATE: raise PermissionError('Desktop control is paused while a private browser or window capture session is active')


def capture_privacy_revision():
    with _LOCK:
        assert_agent_capture_allowed()
        return _PRIVACY_REVISION


def digest(value):
    """Stable content digest; browser skill drafts use it too."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class WindowRecordingService:
    def __init__(self, records, *, port=None, session_valid=None):
        global _PRIVACY_REVISION
        self.records = records
        self.port = port or WindowCapture()
        self.session_valid = session_valid or (lambda principal, session, policy: False)
        # Rows written while recording into skills existed may hold recorded steps and drafts. Nothing reads them now.
        for old in records.list('window-skill'): records.delete('window-skill', old['id'])
        for row in records.list('window-capture'):
            with _LOCK:
                if row['id'] in _PRIVATE: _PRIVACY_REVISION += 1
                _PRIVATE.pop(row['id'], None)
            # A crashed client may still display its last private preview.
            # Reconstruct observation suppression without reviving consent.
            row.pop('steps', None); row.pop('recording', None)
            row.update(stopped=True)
            records.put('window-capture', row['id'], row)
            if row['private']: self._retain_private(row['id'])

    def _retain_private(self, identifier):
        def current():
            row = self.records.get('window-capture', identifier)
            return bool(row and row['private'])
        set_capture_private(identifier, expires_at=float('inf'), valid=current)

    def _expire(self, row):
        if not row['stopped']:
            row.update(stopped=True, revision=row['revision'] + 1)
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
            'window': target, 'private': False, 'stopped': False, 'revision': 1, 'redactions': [], 'expires_at': time() + expires_in})

    def configure(self, identifier, principal, *, private=None, redactions=None):
        global _PRIVACY_REVISION
        row = self.get(identifier, principal)
        if private is not None:
            row['private'] = bool(private)
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
        row.update(stopped=True, private=False, revision=row['revision'] + 1)
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
    def event(event):
        kind = event.get('type')
        if kind == 'pointer':
            if set(event) - {'type', 'action', 'x', 'y', 'button', 'down'} or event.get('action') not in {'click', 'move', 'down', 'up'} or event.get('button', 1) not in {1, 2, 3}: raise ValueError('Unsupported pointer event')
            if any(not isinstance(event.get(key), (int, float)) or not 0 <= event[key] <= 1 for key in ('x', 'y')): raise ValueError('Pointer must remain inside the selected window')
        elif kind == 'text':
            if set(event) - {'type', 'data'} or not isinstance(event.get('data'), str) or len(event['data']) > 2000: raise ValueError('Text must contain at most 2000 characters')
        elif kind == 'key':
            if set(event) - {'type', 'key', 'action'} or event.get('key') not in {'Tab', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Escape', 'Backspace', 'Enter'} or event.get('action') not in {'down', 'up'}: raise ValueError('Only navigation keys may be recorded')
        else: raise ValueError('Unsupported window input')
        return dict(event)

    async def input(self, identifier, principal, event):
        row = self.get(identifier, principal); revision = row['revision']
        event = self.event(event)
        await asyncio.to_thread(self.port.input, row['window'], event)
        current = self.get(identifier, principal)
        if current['revision'] != revision: raise PermissionError('Capture scope changed; input outcome cannot be recorded')
        # What a person types or clicks in their own window is sent to it and kept nowhere.
        return {'ok': True}
