#!/usr/bin/env python3
"""Run SQL conformance against disposable local Docker servers; retain exact-version evidence.

Run with the project's locked environment. Each target owns one loopback-only container,
an ephemeral host port and a temporary TERMX_CONFIG_DIR. No existing database is used.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'pg12': ('postgresql', 'postgres:12', '12', None),
    'pg16': ('postgresql', 'postgres:16', '16', None),
    # The exact MySQL floor is x86-only; Docker Desktop emulates it on Apple Silicon.
    'mysql8017': ('mysql', 'mysql:8.0.17', '8.0.17', 'linux/amd64'),
    'mysql84': ('mysql', 'mysql:8.4', '8.4', None),
    'maria106': ('mariadb', 'mariadb:10.6', '10.6', None),
    'maria1011': ('mariadb', 'mariadb:10.11', '10.11', None),
    'maria118': ('mariadb', 'mariadb:11.8', '11.8', None),
}
URL_ENVS = {'postgresql': 'TERMX_TEST_PG_URL', 'mysql': 'TERMX_TEST_MYSQL_URL',
            'mariadb': 'TERMX_TEST_MARIADB_URL'}
PASSWORD = 'termx-matrix-only'
BASE_TESTS = ('tests/test_data_dialects.py', 'tests/test_data_engines_review.py',
              'tests/test_data_conformance.py', 'tests/test_data_engine_parity.py')
OPTIONAL_TESTS = {
    'tests/test_data_pivot_live.py': ('test_{engine}_live_pivots_and_segments',),
    'tests/test_data_engines_review_phase2.py': ('test_trailing_space_text_stays_a_separate_group[{engine}]',
                                              'test_average_of_whole_numbers_matches_sqlite[{engine}]'),
    'tests/test_data_bi_views.py': ('test_view_dataset_on_live_engines[{engine}]',),
}


def source_identity():
    paths = sorted(set(ROOT.glob('src/termx/data/**/*.py')) | set(ROOT.glob('tests/test_data_*.py')) |
                   set(ROOT.glob('tests/data_*.py')) | {
        ROOT / name for name in ('scripts/test_data_engine_matrix.py', 'tests/test_data_dialects.py',
                                 'tests/test_data_engines_review.py', 'tests/test_data_conformance.py',
                                 'tests/test_data_engine_parity.py', 'tests/data_query_fixture.py',
                                 'tests/data_query_oracle.py', 'tests/conftest.py', 'pyproject.toml', 'uv.lock')})
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    return {'source_commit': commit, 'source_files_sha256': digest.hexdigest(),
            'source_files': [str(path.relative_to(ROOT)) for path in paths]}


def docker(*args, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=True, check=check, timeout=180)


def wait_for_server(url, engine, expected, timeout):
    """A ready endpoint must identify itself as the requested engine and version."""
    source = create_engine(url, connect_args={'connect_timeout': 2})
    deadline = time.monotonic() + timeout
    last_error = None
    try:
        while time.monotonic() < deadline:
            try:
                with source.connect() as conn:
                    version = str(conn.exec_driver_sql('SELECT version()').scalar())
            except Exception as error:  # Only connection/startup errors are retried.
                last_error = error
                time.sleep(1)
                continue
            match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', version)
            if not match:
                raise RuntimeError(f'Unrecognised version: {version}')
            parts = tuple(int(n or 0) for n in match.groups())
            wanted = tuple(int(n) for n in expected.split('.'))
            if parts[:len(wanted)] != wanted:
                raise RuntimeError(f'Expected {engine} {expected}, got {version}')
            if engine == 'postgresql':
                correct_family = version.startswith('PostgreSQL ')
            else:
                correct_family = ('mariadb' in version.lower()) == (engine == 'mariadb')
            if not correct_family:
                raise RuntimeError(f'Expected {engine}, got {version}')
            return version
        raise TimeoutError(f'{engine} startup timed out: {last_error}')
    finally:
        source.dispose()


def read_pytest_evidence(path, engine, modules=()):
    """A green pytest exit alone is insufficient: the selected live checks must pass."""
    root = ET.parse(path).getroot()
    cases = {case.get('name'): case for case in root.findall('.//testcase')}
    required = [f'test_{engine}_live_conformance',
                f'test_live_results_have_the_same_json_types_as_sqlite[{engine}]',
                f'test_the_server_stops_a_runaway_statement_at_the_deadline[{engine}]']
    for module, names in OPTIONAL_TESTS.items():
        if module in modules:
            required += [name.format(engine=engine) for name in names]
    for name in required:
        case = cases.get(name)
        if case is None or any(case.find(status) is not None for status in ('skipped', 'failure', 'error')):
            raise RuntimeError(f'Required live check did not pass: {name}')
    suites = list(root.iter('testsuite'))
    totals = {key: sum(int(suite.get(key, '0')) for suite in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    totals['passed'] = totals['tests'] - totals['failures'] - totals['errors'] - totals['skipped']
    return totals, required


def run_target(target, output_dir, startup_timeout):
    engine, image, expected, platform = TARGETS[target]
    folder = output_dir / target
    folder.mkdir(parents=True, exist_ok=True)
    report = {'target': target, 'engine': engine, 'image': image, 'expected_version': expected,
              'platform': platform, 'started_at': dt.datetime.now(dt.UTC).isoformat(), 'status': 'failed'}
    report.update(source_identity())
    container_name = 'termx-engine-' + target + '-' + uuid.uuid4().hex[:10]
    report['container_name'] = container_name
    try:
        port = '5432' if engine == 'postgresql' else '3306'
        args = ['run', '--rm', '-d', '--name', container_name,
                '--label', 'org.termx.engine-matrix=true', '--cpus', '2', '--memory', '1g',
                '-p', f'127.0.0.1::{port}']
        if platform:
            args += ['--platform', platform]
        if engine == 'postgresql':
            variables = {'POSTGRES_PASSWORD': PASSWORD, 'POSTGRES_USER': 'termx', 'POSTGRES_DB': 'termx'}
        elif engine == 'mysql':
            variables = {'MYSQL_ROOT_PASSWORD': PASSWORD, 'MYSQL_DATABASE': 'termx', 'MYSQL_ROOT_HOST': '%'}
        else:
            variables = {'MARIADB_ROOT_PASSWORD': PASSWORD, 'MARIADB_DATABASE': 'termx', 'MARIADB_ROOT_HOST': '%'}
        for name, value in variables.items():
            args += ['-e', name + '=' + value]
        container = docker(*args, image).stdout.strip()
        info = json.loads(docker('inspect', container).stdout)[0]
        image_info = json.loads(docker('image', 'inspect', info['Image']).stdout)[0]
        report.update(container_id=container, image_id=info['Image'],
                      image_digests=image_info.get('RepoDigests', []), architecture=image_info['Architecture'])
        host_port = info['NetworkSettings']['Ports'][port + '/tcp'][0]['HostPort']
        url = URL.create('postgresql+psycopg' if engine == 'postgresql' else 'mysql+pymysql',
                         username='termx' if engine == 'postgresql' else 'root', password=PASSWORD,
                         host='127.0.0.1', port=int(host_port), database='termx')
        report['server_version'] = wait_for_server(url, engine, expected, startup_timeout)
        print(f'{target}: {report["server_version"]}', flush=True)
        with tempfile.TemporaryDirectory(prefix='termx-engine-profile-') as scratch:
            env = os.environ.copy()
            for name in URL_ENVS.values():
                env.pop(name, None)
            env.update({URL_ENVS[engine]: url.render_as_string(hide_password=False),
                        'TERMX_TEST_SERVER_VERSION': expected, 'TERMX_CONFIG_DIR': scratch,
                        'TERMX_AGENTS_DIR': str(Path(scratch) / 'agents'),
                        'TERMX_ACP_HOME': str(Path(scratch) / 'acp')})
            modules = [*BASE_TESTS, *(name for name in OPTIONAL_TESTS if (ROOT / name).is_file())]
            report['test_modules'] = modules
            command = [sys.executable, '-m', 'pytest', *modules, '-q', '-ra',
                       '--junitxml=' + str(folder / 'pytest.xml')]
            report['command'] = command
            with (folder / 'pytest.log').open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=480)
            report['pytest_exit_code'] = result.returncode
            report['tests'], report['verified_live_checks'] = read_pytest_evidence(folder / 'pytest.xml', engine, modules)
            report['status'] = 'passed' if result.returncode == 0 else 'failed'
            print(f'{target}: {report["status"]}; {report.get("tests", {})}; {folder / "pytest.log"}', flush=True)
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        print(f'{target}: {report["error"]}', flush=True)
    finally:
        try:
            logs = docker('logs', container_name, check=False)
            (folder / 'server.log').write_text(logs.stdout + logs.stderr)
        except Exception as error:
            report.update(status='failed', log_error=f'{type(error).__name__}: {error}')
        finally:
            # Keep the planned name even if docker run timed out after creating it.
            try:
                cleanup = docker('rm', '-fv', container_name, check=False)
                report['container_removed'] = cleanup.returncode == 0 or 'No such container' in cleanup.stderr
                if not report['container_removed']:
                    report.update(status='failed', cleanup_error=cleanup.stderr)
            except Exception as error:
                report.update(status='failed', container_removed=False, cleanup_error=f'{type(error).__name__}: {error}')
        report['finished_at'] = dt.datetime.now(dt.UTC).isoformat()
        (folder / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', choices=list(TARGETS), help='Run selected targets; default is all seven.')
    parser.add_argument('--output-dir', type=Path, required=True, help='Directory for JSON, JUnit and server/test logs.')
    parser.add_argument('--startup-timeout', type=int, default=180)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    docker('info')  # Fail before creating anything if the local daemon is unavailable.
    reports = [run_target(target, output, args.startup_timeout) for target in (args.only or TARGETS)]
    (output / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')
    return 0 if all(report['status'] == 'passed' for report in reports) else 1


if __name__ == '__main__':
    sys.exit(main())
