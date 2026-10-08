"""File-authoritative revisions must bind identical bytes on every platform."""
import hashlib
import os

import pytest

from termx.agent.store import AgentStore
from termx.agents.files import AgentFile
from termx.agents.registry import AgentRegistry, RevisionConflict


def test_saved_revision_matches_disk_under_windows_default_text_translation(tmp_path, monkeypatch):
    store = AgentStore(tmp_path / 'agent.sqlite3')
    registry = AgentRegistry(store, str(tmp_path / 'agents'))
    original = os.fdopen

    def windows_fdopen(fd, mode='r', *args, **kwargs):
        if mode == 'w' and 'newline' not in kwargs:
            kwargs['newline'] = '\r\n'
        return original(fd, mode, *args, **kwargs)

    monkeypatch.setattr(os, 'fdopen', windows_fdopen)
    try:
        saved = registry.save(AgentFile(slug='revision-proof', name='Revision proof',
                                        instructions='Line one\nLíne two\n'))
        path = tmp_path / 'agents' / 'revision-proof.agent.md'
        data = path.read_bytes()
        assert b'\r\n' not in data
        assert saved.revision == hashlib.sha256(data).hexdigest()
        assert registry.load(saved.slug).revision == saved.revision
        assert store.get_custom_agent(saved.qid_id)['file_revision'] == saved.revision
        saved.description = 'Immediate update keeps the original exact CAS.'
        updated = registry.save(saved, expected_revision=saved.revision)
        assert updated.revision == hashlib.sha256(path.read_bytes()).hexdigest()
        assert updated.revision != saved.revision
        with pytest.raises(RevisionConflict):
            registry.save(saved, expected_revision=saved.revision)
    finally:
        store.close()


def test_existing_crlf_source_keeps_exact_revision_and_external_edits_conflict(tmp_path):
    store = AgentStore(tmp_path / 'agent.sqlite3')
    registry = AgentRegistry(store, str(tmp_path / 'agents'))
    try:
        saved = registry.save(AgentFile(slug='legacy', name='Legacy', instructions='One\nTwo\n'))
        path = tmp_path / 'agents' / 'legacy.agent.md'
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        loaded = registry.load('legacy')
        original_revision = loaded.revision
        assert original_revision == hashlib.sha256(path.read_bytes()).hexdigest()
        loaded.description = 'Explicitly reviewed update'
        updated = registry.save(loaded, expected_revision=original_revision)
        assert updated.revision == hashlib.sha256(path.read_bytes()).hexdigest()
        # Preserve existing instruction line endings without translating their
        # CRLF a second time or losing the exact file-authoritative revision.
        assert b'\r\r\n' not in path.read_bytes()
        assert registry.load('legacy').instructions == loaded.instructions
        path.write_bytes(path.read_bytes() + b'\nExternal edit\n')
        with pytest.raises(RevisionConflict):
            registry.save(updated, expected_revision=updated.revision)
    finally:
        store.close()
