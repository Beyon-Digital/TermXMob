"""Fixture and oracle for the pivot, segment and view tests.

`sales` is built to be awkward on purpose: mixed-case and empty-looking text, NULLs in every column, dates across
a year boundary, a text column with exactly 12 distinct values (including NULL), a text column with 14, and tag values
that are SQL, bind markers and alias look-alikes. The oracle shares no code with the compiler.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path

REGIONS = ['East', 'West', 'North', 'east', '(empty)', '', 'South']
DAYS = ['2026-01-05', '2026-02-14', '2026-03-20', '2020-12-31', '2021-01-03', '2026-01-01', '2026-02-01', '2026-03-31']
TAGS = ["O'Brien", '50%', 'a--b', '`tick`', '\U0001F642', 'x' * 200, '(empty)', '', 'pv_c1', ':p1',
        "Robert'); DROP TABLE sales;--"]
SALES = 120


def build_sales() -> dict[str, list[dict]]:
    sales = []
    for i in range(1, SALES + 1):
        day = None if i % 11 == 0 else DAYS[i % 8]
        sales.append({
            'id': i, 'region': None if i % 17 == 0 else REGIONS[i % 7],
            'amount': None if i % 13 == 0 else (i * 37) % 500,
            'price': None if i % 19 == 0 else ((i * 11) % 97) / 4.0,
            'day': day, 'stamp': None if day is None else f'{day} {i % 24:02d}:15:00',
            'flag': None if i % 23 == 0 else i % 3 == 0,
            'tier': None if i == 100 else f't{i % 14:02d}',
            'tag': None if i % 29 == 0 else TAGS[i % 11],
            'qty': i % 5 + 1,
            'customer_id': None if i % 31 == 0 else i % 10 + 1,
            'note': None if i % 7 == 0 else f'note {i}'})
    customers = [{'id': i, 'name': f'Customer {i}', 'segment': 'retail' if i % 2 else 'b2b'} for i in range(1, 11)]
    return {'customers': customers, 'sales': sales}


def create_sales_sqlite(path: Path) -> dict[str, list[dict]]:
    rows = build_sales()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, segment TEXT)')
        db.execute('CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT, amount INTEGER, price REAL, day DATE, stamp DATETIME, '
                   'flag BOOLEAN, tier TEXT, tag TEXT, qty INTEGER, customer_id INTEGER REFERENCES customers(id), note TEXT)')
        for table, items in rows.items():
            names = list(items[0])
            db.executemany(f'INSERT INTO {table}({",".join(names)}) VALUES({",".join("?" * len(names))})',
                           [[int(r[n]) if isinstance(r[n], bool) else r[n] for n in names] for r in items])
    return rows


def bucket_of(day: str | None, bucket: str):
    if day is None:
        return None
    value = dt.date.fromisoformat(day[:10])
    iso = value.isocalendar()
    return {'day': value.isoformat(), 'week': f'{iso[0]}-W{iso[1]:02d}', 'month': f'{value.year}-{value.month:02d}',
            'quarter': f'{value.year}-Q{(value.month - 1) // 3 + 1}', 'year': f'{value.year}'}[bucket]


def aggregate(fn: str, values: list):
    """`values` holds the measure of every row in the cell (None for NULL); count of rows uses a list of 1s."""
    present = [v for v in values if v is not None]
    if fn == 'count':
        return len(present)
    if fn == 'count_distinct':
        return len(set(present))
    if not present:
        return None
    if fn == 'sum':
        return sum(present)
    if fn == 'avg':
        return sum(present) / len(present)
    return min(present) if fn == 'min' else max(present)


def oracle_pivot(rows: list[dict], row_dims: list, column_dim, fn: str, measure=None, where=None):
    """Returns (column_values, {row_key: {column_value: cell, 'total': total}}). Dimensions are functions of a row.
    `measure(row)` is the measured value, or None for a count of rows (every row counts as 1)."""
    rows = [r for r in rows if where is None or where(r)]
    measure = measure or (lambda r: 1)
    columns = sorted({column_dim(r) for r in rows} - {None}) if column_dim else []
    has_null = bool(column_dim) and any(column_dim(r) is None for r in rows)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[tuple(d(r) for d in row_dims)].append(r)
    table = {}
    for key, members in groups.items():
        cells = {}
        for value in [*columns, *([None] if has_null else [])]:
            cells[value] = aggregate(fn, [measure(r) for r in members if column_dim(r) == value])
        cells['total'] = aggregate(fn, [measure(r) for r in members])
        table[key] = cells
    return columns + ([None] if has_null else []), table


def sort_rows(table: dict, ascending: bool = True) -> list[tuple]:
    """Row keys in the documented order for text keys that sort by code point: NULL last."""
    keys = list(table)
    width = len(keys[0]) if keys else 0
    for index in reversed(range(width)):
        keys.sort(key=lambda k: (0, k[index]) if k[index] is not None else (1, 0), reverse=not ascending)
        keys.sort(key=lambda k: k[index] is None)   # stable: NULL last whichever way the rest goes
    return keys
