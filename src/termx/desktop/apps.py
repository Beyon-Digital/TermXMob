"""Application lists for computer-use skills.

A skill names the applications it may drive. Entries are exact bundle IDs,
executable names or paths, or application names as the host reports them: no
patterns. The lists are data from an installed bundle or a schedule, never
authority by themselves.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Sequence
from typing import Any

MAX_APPS = 20
_ENTRY = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._+()/\\:-]{0,199}')
# Key names as the computer controller normalizes them (see agent/computer.py).
_SWITCH_KEYS = {
    'tab': {'meta', 'alt'},
    'grave': {'meta'}, '`': {'meta'},
    'space': {'meta'},
    'escape': {'alt'},
    'h': {'meta'}, 'm': {'meta'}, 'q': {'meta'},
}
# Control plus an arrow moves between Spaces on macOS; elsewhere it is ordinary text navigation.
_MAC_SWITCH_KEYS = {name: {'control'} for name in ('left', 'right', 'up', 'down', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown')}
_SWITCH_KEYS_MAC = {**_SWITCH_KEYS, **_MAC_SWITCH_KEYS}
_MODIFIERS = {'shift', 'control', 'alt', 'meta'}
# The one alias table: the computer controller imports it, so the filter and the keys actually sent cannot disagree.
KEY_ALIASES = {'cmd': 'meta', 'command': 'meta', 'super': 'meta', 'win': 'meta', 'windows': 'meta', 'ctrl': 'control', 'option': 'alt', 'esc': 'escape'}


class AppBlocked(PermissionError):
    """The front application is outside the active application list. ``details`` is the public event payload."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


def verifiable() -> bool:
    """True where the host can name the front application (macOS). Elsewhere a list is checked on a best-effort basis."""
    return sys.platform == 'darwin'


def normalize_apps(value: Any) -> list[str]:
    """One to twenty exact application identifiers, without duplicates. Raises ValueError otherwise."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_APPS:
        raise ValueError(f'Name one to {MAX_APPS} applications')
    apps: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _ENTRY.fullmatch(item) or item != item.strip():
            raise ValueError('Applications are exact bundle IDs, executable names or application names, without patterns')
        if item not in apps:
            apps.append(item)
    return apps


def matches(identity: Iterable[str] | None, apps: Sequence[str]) -> bool:
    """True when any name the front application is known by is exactly one of the listed entries (ignoring case)."""
    names = {str(name).casefold() for name in identity or () if name}
    return bool(names & {app.casefold() for app in apps})


def combine(lists: Iterable[Sequence[str] | None]) -> list[str] | None:
    """The applications every declared list allows. None when no list is declared; an empty result allows nothing."""
    declared = [list(item) for item in lists if item is not None]
    if not declared:
        return None
    return [app for app in declared[0] if all(matches([app], other) for other in declared[1:])]


def _names(value: Any) -> list[str]:
    """The keys one name stands for. Some input backends read 'alt+Tab' as a whole chord, so a name is split on '+'."""
    key = str(value).strip()
    parts = [key] if '+' not in key.strip('+') else [part for part in key.split('+') if part.strip()]
    return [KEY_ALIASES.get(part.strip().lower(), part.strip().lower()) for part in parts]


def switching_hotkey(actions: Iterable[dict[str, Any]]) -> str | None:
    """The first key chord in the batch that moves input to another application, else None.

    Held modifiers carry across key_down/key_up actions, so a chord split over several actions is caught too. Off
    macOS every Windows or Super key use is refused, since its chords are launchers and desktop switching.
    """
    mac = sys.platform == 'darwin'
    switch_keys = _SWITCH_KEYS_MAC if mac else _SWITCH_KEYS
    held: set[str] = set()
    for action in actions:
        kind = str(action.get('type') or '')
        if kind not in {'keypress', 'key_down', 'key_up'}:
            continue
        keys = action.get('keys') or action.get('key') or []
        keys = [keys] if isinstance(keys, str) else list(keys)
        names = [name for key in keys if str(key).strip() for name in _names(key)]
        modifiers = {name for name in names if name in _MODIFIERS}
        regular = [name for name in names if name not in _MODIFIERS]
        if kind == 'key_up':
            held -= modifiers
            continue
        if kind == 'key_down':
            held |= modifiers
        active = modifiers | held
        if not mac and 'meta' in active:
            return '+'.join(sorted(active) + regular)  # Windows and Super chords open launchers, run dialogs and switch desktops
        for name in regular:
            if switch_keys.get(name, set()) & active:
                return '+'.join(sorted(active) + [name])
        if not regular and 'meta' in modifiers and kind == 'keypress':
            return 'meta'  # a lone Super or Windows key opens the launcher
    return None
