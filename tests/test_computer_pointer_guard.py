"""Application lists also cover where the pointer lands, not only which application is in front."""
from __future__ import annotations

import asyncio

import pytest

from termx.agent import computer as computer_module
from termx.agent.computer import ComputerController
from termx.desktop import apps as apps_module
from termx.desktop.apps import AppBlocked
from test_computer_policy import Desktop, anyio_backend  # noqa: F401  (fixture)
from test_computer_skills import _close, _direct


def _controller(monkeypatch):
    sent = []
    monkeypatch.setattr(computer_module, 'list_displays', lambda: [{'id': 'd', 'x': 100, 'y': 50, 'width': 1000, 'height': 800, 'main': True}])
    monkeypatch.setattr(computer_module, 'pointer_target', lambda _id: None)
    monkeypatch.setattr(computer_module, 'apply_event', lambda event, *_: sent.append(event))

    async def frame(self):
        return b'frame'

    monkeypatch.setattr(ComputerController, 'screenshot', frame)
    return ComputerController('d'), sent


def test_every_pointer_event_is_checked_at_its_global_screen_point_before_it_is_sent(monkeypatch):
    async def run():
        controller, sent = _controller(monkeypatch)
        asked = []

        async def pointer(x, y):
            asked.append((x, y))

        batch = [{'type': 'click', 'x': 10, 'y': 20}, {'type': 'double_click', 'x': 30, 'y': 40},
                 {'type': 'drag', 'path': [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}, {'x': 5, 'y': 6}]},
                 {'type': 'scroll', 'x': 7, 'y': 8, 'scroll_y': 3}, {'type': 'mouse_down', 'x': 9, 'y': 9}]
        await controller.execute(batch, pointer_guard=pointer)
        assert asked == [(110, 70), (130, 90), (130, 90), (101, 52), (103, 54), (105, 56), (107, 58), (109, 59)]
        assert len([e for e in sent if e['type'] == 'pointer']) == len(asked) + 1  # the drag's release is never refused

    asyncio.run(run())


def test_a_refused_pointer_event_is_not_sent_and_stops_the_batch(monkeypatch):
    async def run():
        controller, sent = _controller(monkeypatch)

        async def pointer(x, y):
            if x > 120:
                raise AppBlocked('other window', {})

        batch = [{'type': 'double_click', 'x': 10, 'y': 10}, {'type': 'click', 'x': 500, 'y': 10}, {'type': 'type', 'text': 'x'}]
        with pytest.raises(AppBlocked):
            await controller.execute(batch, pointer_guard=pointer)
        assert len([e for e in sent if e['type'] == 'pointer']) == 2  # the double click only
        assert all(e['type'] != 'text' for e in sent)

    asyncio.run(run())


def test_the_manager_blocks_a_click_that_lands_on_another_applications_window(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(apps_module, 'verifiable', lambda: True)
        manager, store_, task, call, events = await _direct(tmp_path, Desktop('Notes'), ['Notes'])
        manager.pointer_allowed = lambda x, y: x < 100  # type: ignore[method-assign]
        with pytest.raises(AppBlocked) as blocked:
            await manager._check_pointer(task['id'], 'batch', ['Notes'], 500, 5)
        assert blocked.value.details['phase'] == 'pointer'
        assert 'computer.app_blocked' in [kind for kind, _ in events]
        await manager._check_pointer(task['id'], 'batch', ['Notes'], 5, 5)
        manager.pointer_allowed = lambda x, y: None  # type: ignore[method-assign]
        await manager._check_pointer(task['id'], 'batch', ['Notes'], 5, 5)  # a list is best effort when the host cannot say
        with pytest.raises(AppBlocked):
            await manager._check_pointer(task['id'], 'batch', ['Notes'], 5, 5, strict=True)
        await _close(manager, store_)

    asyncio.run(run())
