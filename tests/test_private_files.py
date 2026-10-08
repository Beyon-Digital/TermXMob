"""Actual platform ACL integration, using exclusively synthetic fixture files."""
import os
import subprocess
import pytest
from termx.private_files import private_path_permissions, protect_private_path


def test_owner_private_file_and_directory(tmp_path):
    directory = tmp_path / 'private'; directory.mkdir()
    protect_private_path(directory, directory=True)
    assert private_path_permissions(directory)
    secret = directory / 'fixture'; secret.write_bytes(b'synthetic noncredential')
    protect_private_path(secret)
    assert private_path_permissions(secret)
    assert secret.read_bytes() == b'synthetic noncredential'
    if os.name == 'nt':
        subprocess.run(['icacls', str(secret), '/grant', '*S-1-1-0:(R)'], check=True, capture_output=True)
    else:
        secret.chmod(0o644)
    assert not private_path_permissions(secret)
    protect_private_path(secret)
    assert private_path_permissions(secret)


@pytest.mark.skipif(os.name != 'nt', reason='Windows DACL inheritance integration')
def test_sqlite_journal_inherits_owner_only_windows_directory_policy(tmp_path):
    import sqlite3
    directory = tmp_path / 'auth'; directory.mkdir()
    protect_private_path(directory, directory=True)
    connection = sqlite3.connect(directory / 'identity.sqlite3')
    try:
        connection.execute('CREATE TABLE fixture(value TEXT)'); connection.commit()
        connection.execute("INSERT INTO fixture VALUES ('synthetic value')")
        journal = directory / 'identity.sqlite3-journal'
        assert journal.is_file()
        # Inherited ACLs are safe even though not explicitly protected: each
        # inheritable ACE is restricted to the owner and privileged OS accounts.
        assert private_path_permissions(journal, require_protected=False)
    finally: connection.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows user-bound DPAPI integration')
def test_backend_api_credentials_use_real_dpapi_and_private_acl(tmp_path, monkeypatch):
    from termx.agent.secrets import CredentialStore
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(tmp_path / 'fixture-config'))
    credentials = CredentialStore()
    identifier, secret = 'synthetic-windows-dpapi-fixture', 'synthetic-noncredential-value'
    try:
        credentials.set(identifier, secret)
        path = credentials._windows_path(identifier)
        assert private_path_permissions(path.parent)
        assert private_path_permissions(path)
        assert secret.encode() not in path.read_bytes()
        assert credentials.get(identifier) == secret
    finally: credentials.delete(identifier)
    assert credentials.get(identifier) is None
