"""Application lists for desktop-control skills and schedules.

A skill names the applications it may drive. Entries are exact bundle IDs,
executable names or paths, or application names as the host reports them: no
patterns. The lists are data from an installed bundle or a schedule, never
authority by themselves.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

MAX_APPS = 20
_ENTRY = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._+()/\\:-]{0,199}')


class AppBlocked(PermissionError):
    """An application is outside the active application list. ``details`` is the public event payload."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


def verifiable() -> bool:
    """True where this host's platform has a pinned, verifiable agent-desktop build, so a list can be enforced."""
    from termx.desktop.agent_desktop import get_client

    return get_client().pin_complete()


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
    """True when any name the application is known by is exactly one of the listed entries (ignoring case)."""
    names = {str(name).casefold() for name in identity or () if name}
    return bool(names & {app.casefold() for app in apps})


def _is_identifier(entry: str) -> bool:
    """A bundle id or executable name (dotted, no spaces, or an .exe name) as opposed to a display name."""
    return (" " not in entry and "." in entry) or entry.casefold().endswith(".exe")


def matches_app(name: str, bundle_id: str, executable: str, apps: Sequence[str]) -> bool:
    """Whether an application is on a list. Identifier entries compare only with the bundle id or executable and
    display-name entries only with the display name, so an application cannot satisfy an identifier by naming itself
    after it."""
    for entry in apps:
        folded = entry.casefold()
        if _is_identifier(entry):
            if folded and folded in {bundle_id.casefold(), executable.casefold()}:
                return True
        elif folded == name.casefold():
            return True
    return False


def combine(lists: Iterable[Sequence[str] | None]) -> list[str] | None:
    """The applications every declared list allows. None when no list is declared; an empty result allows nothing."""
    declared = [list(item) for item in lists if item is not None]
    if not declared:
        return None
    return [app for app in declared[0] if all(matches([app], other) for other in declared[1:])]
