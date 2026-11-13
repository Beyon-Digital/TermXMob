"""The live version gate must reject skipped coverage and always try to remove its own server."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'test_data_engine_matrix.py'
SPEC = importlib.util.spec_from_file_location('termx_engine_matrix', SCRIPT)
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


def junit(path, blocked=None, missing=False):
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests='4', failures='0', errors='0', skipped='1')
    if not missing:
        selected = ET.SubElement(suite, 'testcase', name='test_postgresql_live_conformance')
        if blocked:
            ET.SubElement(selected, blocked)
    ET.SubElement(suite, 'testcase', name='test_live_results_have_the_same_json_types_as_sqlite[postgresql]')
    ET.SubElement(suite, 'testcase', name='test_the_server_stops_a_runaway_statement_at_the_deadline[postgresql]')
    ET.SubElement(ET.SubElement(suite, 'testcase', name='test_mysql_live_conformance'), 'skipped')
    ET.ElementTree(root).write(path)


def test_matrix_requires_selected_live_checks_but_allows_other_engine_skips(tmp_path):
    path = tmp_path / 'pytest.xml'
    junit(path)
    totals, verified = matrix.read_pytest_evidence(path, 'postgresql')
    assert totals['passed'] == 3 and totals['skipped'] == 1
    assert 'test_postgresql_live_conformance' in verified


@pytest.mark.parametrize('blocked,missing', [('skipped', False), ('failure', False), ('error', False), (None, True)])
def test_matrix_rejects_green_pytest_with_missing_or_skipped_live_suite(tmp_path, blocked, missing):
    path = tmp_path / 'pytest.xml'
    junit(path, blocked, missing)
    with pytest.raises(RuntimeError, match='Required live check did not pass'):
        matrix.read_pytest_evidence(path, 'postgresql')


@pytest.mark.parametrize('module,names', list(matrix.OPTIONAL_TESTS.items()))
def test_matrix_requires_present_later_phase_live_suites(tmp_path, module, names):
    path = tmp_path / 'pytest.xml'
    junit(path)
    with pytest.raises(RuntimeError, match='Required live check did not pass'):
        matrix.read_pytest_evidence(path, 'postgresql', [module])
    root = ET.parse(path)
    for name in names:
        ET.SubElement(root.find('testsuite'), 'testcase', name=name.format(engine='postgresql'))
    root.write(path)
    _, verified = matrix.read_pytest_evidence(path, 'postgresql', [module])
    assert all(name.format(engine='postgresql') in verified for name in names)


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_matrix_attempts_cleanup_and_writes_evidence_after_start_and_log_timeouts(tmp_path, monkeypatch, cleanup_fails):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] in ('run', 'logs'):
            raise subprocess.TimeoutExpired('docker ' + args[0], 1)
        assert args[:2] == ('rm', '-fv')
        return SimpleNamespace(returncode=int(cleanup_fails), stdout='', stderr='daemon unavailable' if cleanup_fails else '')

    monkeypatch.setattr(matrix, 'docker', docker)
    report = matrix.run_target('pg12', tmp_path, 1)
    planned_name = calls[0][calls[0].index('--name') + 1]
    assert calls[1] == ('logs', planned_name)
    assert calls[2] == ('rm', '-fv', planned_name)
    assert report['status'] == 'failed' and report['container_removed'] is not cleanup_fails
    assert 'log_error' in report and 'TimeoutExpired' in report['error']
    assert json.loads((tmp_path / 'pg12' / 'result.json').read_text()) == report
