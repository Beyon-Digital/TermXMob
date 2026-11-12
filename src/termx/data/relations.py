"""Join suggestions from declared foreign keys and conservative name matching."""
from __future__ import annotations

from termx.data.dialects.base import Dialect
from termx.data.query_spec import SchemaIndex, TableInfo, _compatible

MAX_RELATIONS = 200
_GENERIC = {'id'}


def _variants(stem: str) -> set[str]:
    """Singular and plural spellings of a lower-case stem: s, es, ies/y."""
    forms = {stem, stem + 's', stem + 'es'}
    if stem.endswith('y'):
        forms.add(stem[:-1] + 'ies')
    if stem.endswith('ies'):
        forms.add(stem[:-3] + 'y')
    if stem.endswith('es'):
        forms.add(stem[:-2])
    if stem.endswith('s'):
        forms.add(stem[:-1])
    return forms


def _title(name: str) -> str:
    text = name.replace('_', ' ')
    return text[:1].upper() + text[1:]


def _sentence(child: str, child_columns: list[str], parent: str, parent_columns: list[str]) -> str:
    verb = 'matches' if len(child_columns) == 1 else 'match'
    return f"{_title(child)} to {_title(parent)}: {', '.join(child_columns)} {verb} {', '.join(parent_columns)}"


def _single_key(table: TableInfo):
    return table.columns[table.primary_key[0]] if len(table.primary_key) == 1 else None


def suggest_relations(index: SchemaIndex, dialect: Dialect, table: str | None = None) -> list[dict]:
    declared: dict[tuple, dict] = {}
    for child in index.tables.values():
        if not child.readable:
            continue
        for key in child.foreign_keys:
            parent = index.tables.get(key['referred_table'])
            columns, referred = list(key['columns']), list(key['referred_columns'])
            if parent is None or not parent.readable or not columns or len(columns) != len(referred):
                continue
            if any(c not in child.columns for c in columns) or any(c not in parent.columns for c in referred):
                continue
            declared[(child.name, tuple(columns), parent.name, tuple(referred))] = _relation(
                child.name, columns, parent.name, referred, 'foreign_key', 'declared')
    likely: dict[tuple, dict] = {}
    by_lower: dict[str, list[TableInfo]] = {}
    for candidate in index.tables.values():
        by_lower.setdefault(candidate.name.lower(), []).append(candidate)

    def consider(child: TableInfo, column: str, parent: TableInfo) -> None:
        key_column = _single_key(parent)
        if key_column is None or parent.name == child.name:
            return
        if not _compatible(dialect, child.columns[column].kind, key_column.kind):
            return
        key = (child.name, (column,), parent.name, (key_column.name,))
        if key not in declared and key not in likely:
            likely[key] = _relation(child.name, [column], parent.name, [key_column.name], 'name_match', 'likely')

    for child in index.tables.values():
        if not child.readable:
            continue
        for column in child.columns:
            lowered = column.lower()
            if lowered in _GENERIC:
                continue
            # customer_id / customerid -> customers
            for suffix in ('_id', 'id'):
                if lowered.endswith(suffix) and len(lowered) > len(suffix):
                    stem = lowered[: -len(suffix)].rstrip('_')
                    for form in _variants(stem):
                        for parent in by_lower.get(form, ()):
                            if parent.readable:
                                consider(child, column, parent)
                    break
            # the same column name is the primary key of exactly one table
            owners = [t for t in index.tables.values() if t.readable and t.primary_key == (column,)]
            if len(owners) == 1 and owners[0].name != child.name:
                consider(child, column, owners[0])

    def order(item: dict):
        return (item['child_table'], item['child_columns'], item['parent_table'], item['parent_columns'])

    results = sorted(declared.values(), key=order) + sorted(likely.values(), key=order)
    if table is not None:
        results = [r for r in results if table in (r['child_table'], r['parent_table'])]
    return results[:MAX_RELATIONS]


def _relation(child, child_columns, parent, parent_columns, source, confidence) -> dict:
    return {'child_table': child, 'child_columns': child_columns, 'parent_table': parent,
            'parent_columns': parent_columns, 'source': source, 'confidence': confidence,
            'cardinality': 'many_to_one', 'sentence': _sentence(child, child_columns, parent, parent_columns)}
