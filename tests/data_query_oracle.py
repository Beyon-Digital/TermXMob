"""An independent Python evaluation of a QuerySpec over the fixture rows.

It shares no code with the compiler. It encodes the documented semantics: NULLs sort last,
`ne` and `not_in` keep rows whose value is empty, text matching is case-insensitive with the
engine's folding, a date-only value against a datetime column means the whole day, and ISO
8601 weeks. Tests compare its output with what each engine returns for the compiled SQL.
"""
from __future__ import annotations

import datetime as dt
import decimal
from collections import defaultdict

from termx.data.query_spec import FilterGroup, OutputSort, QuerySpec

KINDS = {
    'customers': {'id': 'integer', 'name': 'text', 'region': 'text', 'created_on': 'date'},
    'orders': {'id': 'integer', 'customer_id': 'integer', 'status': 'text', 'amount': 'number',
               'ordered_at': 'datetime', 'note': 'text', 'paid': 'boolean'},
    'order_items': {'order_id': 'integer', 'sku': 'text', 'qty': 'integer'},
}
PRIMARY_KEYS = {'customers': ['id'], 'orders': ['id'], 'order_items': ['order_id', 'sku']}


def ascii_lower(text: str) -> str:
    return ''.join(c.lower() if c < '\x80' else c for c in text)


def bucket_label(value, bucket: str):
    if value is None:
        return None
    day = value.date() if isinstance(value, dt.datetime) else value
    iso = day.isocalendar()
    return {'day': day.isoformat(), 'week': f'{iso[0]}-W{iso[1]:02d}', 'month': f'{day.year}-{day.month:02d}',
            'quarter': f'{day.year}-Q{(day.month - 1) // 3 + 1}', 'year': f'{day.year}'}[bucket]


def _convert(value, kind):
    if kind in ('integer', 'number'):
        return float(value)
    if kind == 'boolean':
        return bool(value) if not isinstance(value, str) else value.lower() in ('true', '1', 'yes')
    if kind == 'date':
        return dt.date.fromisoformat(value)
    if kind == 'datetime':
        if len(value) == 10:
            return dt.date.fromisoformat(value)
        return dt.datetime.fromisoformat(value)
    return str(value)


class Oracle:
    def __init__(self, tables: dict[str, list[dict]], fold=str.lower, collation_equal: bool = False):
        self.tables, self.fold, self.ci = tables, fold, collation_equal

    def evaluate(self, spec: QuerySpec):
        """Returns (rows, labels, kinds, sort_is_text) with up to limit+1 rows."""
        aliases = {spec.from_.alias: spec.from_.table, **{j.alias: j.table for j in spec.joins}}
        combined = [{spec.from_.alias: row} for row in self.tables[spec.from_.table]]
        for join in spec.joins:
            nxt = []
            for current in combined:
                matches = [r for r in self.tables[join.table] if all(
                    (current[p.left.table_alias] or {}).get(p.left.column) is not None
                    and current[p.left.table_alias][p.left.column] == r[p.right.column] for p in join.on)]
                if matches:
                    nxt += [{**current, join.alias: m} for m in matches]
                elif join.kind == 'left':
                    nxt.append({**current, join.alias: None})
            combined = nxt
        combined = [c for c in combined if self.group(spec.filters, c, aliases)]

        def value(c, alias, column):
            row = c[alias]
            return None if row is None else row[column]

        def kind(alias, column):
            return KINDS[aliases[alias]][column]

        summary = bool(spec.group_by or spec.aggregates)
        outputs = []   # (label, kind, getter(list-of-combined-rows) , text_key)
        if summary:
            groups = defaultdict(list)
            for c in combined:
                key = tuple(bucket_label(value(c, g.table_alias, g.column), g.bucket) if g.bucket
                            else value(c, g.table_alias, g.column) for g in spec.group_by)
                groups[key].append(c)
            if not spec.group_by:
                groups.setdefault((), [])
            rows, labels, kinds, sort_keys = [], [], [], []
            for g in spec.group_by:
                labels.append(g.label or (f'{g.column} by {g.bucket}' if g.bucket else g.column))
                kinds.append('text' if g.bucket else kind(g.table_alias, g.column))
            for a in spec.aggregates:
                labels.append(a.label or self.default_agg_label(a))
                kinds.append('aggregate')
            for key, members in groups.items():
                row = list(key)
                for a in spec.aggregates:
                    row.append(self.aggregate(a, members, value))
                rows.append(row)
            group_count = len(spec.group_by)
            tie = list(range(group_count))
            text_tie = [i for i in tie if kinds[i] == 'text' and not spec.group_by[i].bucket]
            outs_by_label = {l: i for i, l in enumerate(labels)}

            def sort_index(key):
                if isinstance(key, OutputSort):
                    return outs_by_label[key.output]
                return next(i for i, g in enumerate(spec.group_by) if (g.table_alias, g.column) == (key.table_alias, key.column))
            order = [(sort_index(k), k.direction) for k in spec.sort]
            text_sort = bool(text_tie) or any(kinds[i] == 'text' and not (i < group_count and spec.group_by[i].bucket) for i, _ in order)
            keyed = [(order, tie)]
        else:
            select = spec.select or [None]
            if spec.select:
                cols = [(s.table_alias, s.column, s.label) for s in spec.select]
            else:
                cols = [(a, c, None) for a, t in aliases.items() for c in KINDS[t]]
            names = [c[2] or c[1] for c in cols]
            dupes = {n for n in names if names.count(n) > 1}
            labels = [c[2] or (f'{c[0]}.{c[1]}' if c[1] in dupes else c[1]) for c in cols]
            kinds = [kind(a, c) for a, c, _ in cols]
            rows = [[value(c, a, col) for a, col, _ in cols] for c in combined]
            carried = [(c, [value(c, a, col) for a, col, _ in cols]) for c in combined]
            by_label = {l: i for i, l in enumerate(labels)}
            # Sorting happens on the combined rows so unselected columns can be used.
            def key_of(k):
                if isinstance(k, OutputSort):
                    i = by_label[k.output]
                    a, col, _ = cols[i]
                    return a, col
                return k.table_alias, k.column
            order_cols = [(key_of(k), k.direction) for k in spec.sort]
            tie_cols = [(spec.from_.alias, p) for p in PRIMARY_KEYS[spec.from_.table]]
            for j in spec.joins:
                tie_cols += [(j.alias, p) for p in PRIMARY_KEYS[j.table]]
            text_sort = any(kind(a, c) == 'text' for (a, c), _ in order_cols)
            ordered = self.sort_rows(carried, [(lambda c, a=a, col=col: value(c, a, col), d) for (a, col), d in order_cols]
                                     + [(lambda c, a=a, col=col: value(c, a, col), 'asc') for a, col in tie_cols])
            sliced = ordered[spec.offset: spec.offset + spec.limit + 1]
            return [r for _, r in sliced], labels, kinds, text_sort
        ordered = self.sort_rows([(r, r) for r in rows],
                                 [(lambda r, i=i: r[i], d) for i, d in order] + [(lambda r, i=i: r[i], 'asc') for i in tie])
        sliced = ordered[spec.offset: spec.offset + spec.limit + 1]
        return [r for _, r in sliced], labels, kinds, text_sort

    @staticmethod
    def sort_rows(rows, keys):
        for getter, direction in reversed(keys):
            nulls = [r for r in rows if getter(r[0]) is None]
            present = [r for r in rows if getter(r[0]) is not None]
            present.sort(key=lambda r: getter(r[0]), reverse=direction == 'desc')
            rows = present + nulls
        return rows

    @staticmethod
    def default_agg_label(a):
        if a.fn == 'count' and a.column is None:
            return 'Number of rows'
        return {'count': 'Number of {}', 'count_distinct': 'Unique {}', 'sum': 'Total {}', 'avg': 'Average {}',
                'min': 'Smallest {}', 'max': 'Largest {}'}[a.fn].format(a.column)

    @staticmethod
    def aggregate(a, members, value):
        if a.column is None:
            return len(members)
        values = [v for v in (value(m, a.table_alias, a.column) for m in members) if v is not None]
        if a.fn == 'count':
            return len(values)
        if a.fn == 'count_distinct':
            return len(set(values))
        if not values:
            return None
        if a.fn == 'sum':
            return sum(values)
        if a.fn == 'avg':
            return sum(values) / len(values)
        return min(values) if a.fn == 'min' else max(values)

    # --- filters ---------------------------------------------------------------------------
    def group(self, node, c, aliases):
        if isinstance(node, FilterGroup):
            results = (self.group(i, c, aliases) for i in node.items)
            return all(results) if node.op == 'and' else any(results)
        return self.leaf(node, c, aliases)

    def leaf(self, node, c, aliases):
        row = c[node.field.table_alias]
        v = None if row is None else row[node.field.column]
        kind = KINDS[aliases[node.field.table_alias]][node.field.column]
        op = node.op
        if op == 'is_null':
            return v is None
        if op == 'not_null':
            return v is not None
        if op in ('contains', 'starts_with'):
            if v is None:
                return False
            text = v.isoformat(sep=' ') if isinstance(v, dt.datetime) else (str(int(v)) if kind == 'boolean' else str(v))
            if kind == 'number':
                text = repr(float(v))
            needle = self.fold(node.value)
            haystack = self.fold(text)
            return needle in haystack if op == 'contains' else haystack.startswith(needle)
        raw = node.values if node.values is not None else [node.value]
        wanted = [_convert(x, kind) for x in raw]
        if v is None:
            return op in ('ne', 'not_in')
        if kind == 'text' and self.ci:
            v, wanted = self.fold(v), [self.fold(w) for w in wanted]
        if kind == 'datetime':
            return self.datetime_leaf(op, v, wanted)
        if op == 'eq':
            return v == wanted[0]
        if op == 'ne':
            return v != wanted[0]
        if op == 'gt':
            return v > wanted[0]
        if op == 'gte':
            return v >= wanted[0]
        if op == 'lt':
            return v < wanted[0]
        if op == 'lte':
            return v <= wanted[0]
        if op == 'between':
            return wanted[0] <= v <= wanted[1]
        if op == 'in':
            return v in wanted
        return v not in wanted

    @staticmethod
    def datetime_leaf(op, v, wanted):
        def span(w):
            if isinstance(w, dt.datetime):
                return w, w
            start = dt.datetime.combine(w, dt.time())
            return start, start + dt.timedelta(days=1)

        def inside(w):
            lo, hi = span(w)
            return v == lo if lo == hi else lo <= v < hi
        if op == 'eq':
            return inside(wanted[0])
        if op == 'ne':
            return not inside(wanted[0])
        lo, hi = span(wanted[0])
        if op == 'gt':
            return v >= hi if lo != hi else v > lo
        if op == 'gte':
            return v >= lo
        if op == 'lt':
            return v < lo
        if op == 'lte':
            return v < hi if lo != hi else v <= lo
        if op == 'between':
            lo2, hi2 = span(wanted[1])
            return lo <= v and (v < hi2 if lo2 != hi2 else v <= lo2)
        if op == 'in':
            return any(inside(w) for w in wanted)
        return not any(inside(w) for w in wanted)


def normalise(value):
    """Make engine results comparable: ints for booleans, rounded floats, ISO text for dates."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (decimal.Decimal, float)):
        return round(float(value), 5)
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=' ')
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, int):
        return value
    return value


def normalise_rows(rows):
    return [[normalise(cell) for cell in row] for row in rows]
