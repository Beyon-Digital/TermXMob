"""Shared fixtures for the visual query builder tests.

`customers`, `orders` and `order_items` hold deterministic rows. The same rows are loaded into
SQLite here and, for the env-gated live tests, into PostgreSQL, MySQL and MariaDB.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from types import SimpleNamespace

PASSWORD = 'Data-query-fixture-password-123'

SPECIAL_ORDER_DATES = [
    dt.datetime(2020, 12, 31, 23, 59, 59), dt.datetime(2021, 1, 3, 0, 0, 0), dt.datetime(2024, 2, 29, 12, 0, 0),
    dt.datetime(2026, 1, 1, 0, 0, 0), dt.datetime(2026, 11, 12, 14, 30, 0), dt.datetime(2027, 1, 1, 8, 0, 0),
    dt.datetime(2026, 11, 12, 0, 0, 0), dt.datetime(2026, 11, 12, 23, 59, 59), dt.datetime(2026, 11, 13, 0, 0, 0),
]
SPECIAL_NAMES = {1: 'Éclair', 2: '100%_done', 3: 'a!b', 4: 'ALICE', 5: 'alice', 6: None, 7: "it's", 8: 'under_score'}
SPECIAL_NOTES = {1: 'late delivery', 2: '50%_off', 3: "it's!", 4: None, 5: 'LATE payment', 6: 'ok'}


def build_rows() -> dict[str, list[dict]]:
    customers = []
    for i in range(1, 41):
        customers.append({
            'id': i, 'name': SPECIAL_NAMES[i] if i in SPECIAL_NAMES else f'Customer {i:02d}',
            'region': None if i % 9 == 0 else ['north', 'south', 'east', 'west'][i % 4],
            'created_on': None if i % 11 == 0 else dt.date(2020, 1, 1) + dt.timedelta(days=30 * i)})
    orders = []
    for i in range(1, 1306):
        if i <= len(SPECIAL_ORDER_DATES):
            placed = SPECIAL_ORDER_DATES[i - 1]
        elif i % 71 == 0:
            placed = None
        else:
            placed = dt.datetime(2020, 12, 1, 9, 15, 0) + dt.timedelta(days=(i * 3) % 2900, minutes=(i * 7) % 600)
        orders.append({
            'id': i, 'customer_id': None if i % 97 == 0 else (i % 40) + 1,
            'status': None if i % 53 == 0 else ['open', 'paid', 'closed', 'void'][i % 4],
            'amount': None if i % 61 == 0 else float((i * 37) % 500) + (i % 7) * 0.5,
            'ordered_at': placed,
            'note': SPECIAL_NOTES.get(i, 'standard' if i % 5 else None),
            'paid': None if i % 79 == 0 else i % 3 == 0})
    items = []
    for order_id in range(1, 301):
        for k in range(1, 1 + order_id % 3 + 1):
            items.append({'order_id': order_id, 'sku': f'SKU-{k}', 'qty': (order_id * k) % 5 + 1})
    return {'customers': customers, 'orders': orders, 'order_items': items}


def sqlite_text(value):
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def create_sqlite(path: Path) -> dict[str, list[dict]]:
    rows = build_rows()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, region TEXT, created_on DATE)')
        db.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), '
                   'status TEXT, amount REAL, ordered_at DATETIME, note TEXT, paid BOOLEAN)')
        db.execute('CREATE TABLE order_items(order_id INTEGER REFERENCES orders(id), sku TEXT, qty INTEGER, '
                   'PRIMARY KEY(order_id, sku))')
        db.execute('CREATE TABLE weird(id INTEGER PRIMARY KEY, "we:ird" TEXT)')
        db.execute('CREATE TABLE loose(a, b)')
        db.execute('CREATE VIEW order_totals AS SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id')
        db.execute("CREATE VIEW unsafe_view AS SELECT load_extension('/nonexistent/extension') AS dangerous")
        for table, items in rows.items():
            if not items:
                continue
            names = list(items[0])
            db.executemany(f'INSERT INTO {table}({",".join(names)}) VALUES({",".join("?" * len(names))})',
                           [[sqlite_text(row[n]) if not isinstance(row[n], bool) else int(row[n]) if row[n] is not None else None
                             for n in names] for row in items])
        db.execute("INSERT INTO weird VALUES(1, 'x')")
        db.executemany('INSERT INTO loose VALUES(?,?)', [(1, 'a'), (2, 'b')])
    return rows


def make_fixture(tmp_path, monkeypatch) -> SimpleNamespace:
    """Identities (owner, editor, viewer, outsider), a workspace and registered sources."""
    from termx.agent.secrets import CredentialStore
    from termx.data.service import DataService
    from termx.identity import AuthenticationService

    host = tmp_path / 'host'
    monkeypatch.setenv('TERMX_CONFIG_DIR', str(host))
    identity = AuthenticationService(host / 'identity.sqlite3')
    alice = identity.setup_owner('alice', PASSWORD)
    bob = identity.create_local_user('bob', PASSWORD, [])
    viewer = identity.create_local_user('vera', PASSWORD, [])
    outsider = identity.create_local_user('outsider', PASSWORD, ['host-admin'])
    state = SimpleNamespace(identity=identity, credentials=CredentialStore(memory={}),
                            store=SimpleNamespace(path=host / 'config.json'))
    service = state.data_studio = DataService(state, tmp_path / 'metadata')
    wid = service.create_workspace(alice, 'Orders analysis')['id']
    service.add_member(alice, wid, username='bob', role='editor')
    service.add_member(alice, wid, username='vera', role='viewer')
    path = tmp_path / 'shop.sqlite3'
    rows = create_sqlite(path)
    runtime = service.runtime
    source = runtime.create_connection(alice, wid, {'name': 'Shop', 'engine': 'sqlite', 'config': {'path': str(path)}, 'shared_read': True})
    private = runtime.create_connection(alice, wid, {'name': 'Private', 'engine': 'sqlite', 'config': {'path': str(path)}, 'shared_read': False})
    other_wid = service.create_workspace(alice, 'Elsewhere')['id']
    foreign = runtime.create_connection(alice, other_wid, {'name': 'Foreign', 'engine': 'sqlite', 'config': {'path': str(path)}, 'shared_read': True})
    sessions = {}
    return SimpleNamespace(root=tmp_path, host=host, identity=identity, state=state, service=service, runtime=runtime,
        actors={'alice': alice, 'bob': bob, 'vera': viewer, 'outsider': outsider}, sessions=sessions,
        wid=wid, other_wid=other_wid, path=path, rows=rows, source=source, private=private, foreign=foreign)
