"""validate_read must accept the boolean connectors and date functions that compiled
specs emit, and keep rejecting side-effecting functions."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from termx.data.connectors import SAFE_FUNCTIONS, validate_read


ACCEPTED = [
    ('sqlite', 'SELECT 1 FROM t WHERE a = 1 AND b = 2'),
    ('sqlite', 'SELECT 1 FROM t WHERE a = 1 OR b = 2'),
    ('sqlite', 'SELECT 1 FROM t WHERE (a = 1 OR b = 2) AND (c = 3 OR (d = 4 AND e = 5))'),
    ('postgresql', 'SELECT 1 FROM t WHERE a = 1 AND (b = 2 OR c IS NULL)'),
    ('mysql', 'SELECT 1 FROM t WHERE a = 1 AND (b = 2 OR c IS NULL)'),
    ('mariadb', 'SELECT 1 FROM t WHERE a = 1 AND (b = 2 OR c IS NULL)'),
    ('sqlite', "SELECT strftime('%Y-%m', d) FROM t"),
    ('sqlite', "SELECT printf('%s-W%02d', strftime('%Y', date(d,'-3 days','weekday 4')), 3) FROM t"),
    ('postgresql', "SELECT to_char(d,'YYYY-MM-DD') FROM t"),
    ('postgresql', "SELECT to_char(d,'IYYY-\"W\"IW') FROM t"),
    ('mysql', "SELECT date_format(d,'%x-W%v') FROM t"),
    ('mariadb', "SELECT date_format(d,'%x-W%v') FROM t"),
    ('mysql', "SELECT CONCAT(date_format(d,'%Y'),'-Q',CAST(quarter(d) AS CHAR)) FROM t"),
    ('sqlite', "SELECT strftime('%Y', d) || '-Q' || ((CAST(strftime('%m', d) AS INTEGER) + 2) / 3) FROM t"),
    ('sqlite', "SELECT 1 FROM t WHERE LOWER(CAST(a AS TEXT)) LIKE LOWER(:p) ESCAPE '!'"),
    ('sqlite', 'SELECT a FROM t ORDER BY a DESC NULLS LAST'),
    ('sqlite', 'SELECT SUM(CASE WHEN a = 1 THEN b END) FROM t'),
    ('mysql', 'SELECT COUNT(DISTINCT a) FROM t ORDER BY (a IS NULL), a ASC'),
]


@pytest.mark.parametrize('engine,sql', ACCEPTED)
def test_validate_read_accepts_and_or_and_date_functions(engine, sql):
    parameters = {'p': 'x'} if ':p' in sql else None
    assert validate_read(sql, engine, parameters)[0] == sql


REJECTED = [
    ('sqlite', "SELECT load_extension('x')"),
    ('sqlite', "SELECT readfile('/etc/passwd')"),
    ('postgresql', "SELECT pg_read_file('/etc/passwd')"),
    ('postgresql', "SELECT lo_import('/etc/passwd')"),
    ('postgresql', 'SELECT pg_sleep(1)'),
    ('mysql', 'SELECT sleep(5)'),
    ('mysql', 'SELECT 1 FROM t WHERE a XOR b'),
    ('mariadb', 'SELECT 1 FROM t WHERE a XOR b'),
    ('sqlite', 'SELECT 1; DELETE FROM t'),
]


@pytest.mark.parametrize('engine,sql', REJECTED)
def test_validate_read_still_rejects_side_effects_and_xor(engine, sql):
    with pytest.raises(HTTPException) as raised:
        validate_read(sql, engine)
    assert raised.value.status_code == 422


def test_allowlist_additions_are_exactly_the_documented_names():
    for name in ('date_format', 'quarter', 'time_to_str', 'ts_or_ds_to_timestamp', 'and', 'or'):
        assert name in SAFE_FUNCTIONS
    assert 'xor' not in SAFE_FUNCTIONS
