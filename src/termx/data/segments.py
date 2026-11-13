"""Segments: saved filter groups that other queries reuse.

A segment is a view of kind `segment`: a base table, its combined tables and a filter tree. A query uses it
with a `{segment: id}` filter node. The compiler inlines the segment's filters into the same statement (same
base table, no subquery), so the read-only validator and the network connector need no new rules.

This module is pure. It holds the data shapes, the alias matching rule and the reference graph checks that the
compiler and the view service share. Loading segment records is the caller's job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError

from termx.data.query_spec import (SEGMENT_MAX_DEPTH, SEGMENT_MAX_EXPANSIONS, SEGMENT_MAX_LEAVES, FilterGroup, FilterSegment, Join, QuerySpec, SpecError, TableRef)


@dataclass(frozen=True)
class ResolvedSegment:
    id: str
    name: str
    revision: int
    source: str
    spec: QuerySpec

    @property
    def base(self) -> TableRef:
        return self.spec.from_

    @property
    def joins(self) -> list[Join]:
        return self.spec.joins

    @property
    def filters(self) -> FilterGroup:
        return self.spec.filters


SegmentSet = dict[str, ResolvedSegment]

MISSING = 'The segment this filter uses was deleted. Remove the filter or pick another segment.'


def cycle_message(names: tuple[str, ...]) -> str:
    chain = ', '.join(f"'{n}'" for n in names)
    return ('A segment cannot use itself.' if not names else
            f'These segments use each other in a loop: {chain}. Remove one of the segment filters.')


def resolve_record(record: dict) -> ResolvedSegment | None:
    """A stored view record as a segment, or None when it is not a usable segment."""
    if not isinstance(record, dict) or record.get('archived') or record.get('view_kind') != 'segment':
        return None
    try:
        spec = QuerySpec.model_validate(record.get('spec'))
    except ValidationError:
        return None
    return ResolvedSegment(record['id'], str(record.get('name') or ''), int(record.get('revision') or 0), spec.source, spec)


def references_of(group: FilterGroup) -> list[str]:
    """Segment ids used directly in a filter tree, first use first, without repeats."""
    found: list[str] = []
    stack: list = [group]
    while stack:
        node = stack.pop(0)
        if isinstance(node, FilterGroup):
            stack[0:0] = list(node.items)
        elif isinstance(node, FilterSegment) and node.segment not in found:
            found.append(node.segment)
    return found


def count_leaves(group: FilterGroup) -> int:
    total = 0
    for item in group.items:
        total += count_leaves(item) if isinstance(item, FilterGroup) else 1
    return total


def map_aliases(host_from: TableRef, host_joins: list[Join], segment: ResolvedSegment) -> tuple[dict[str, str] | None, tuple[str, str] | None]:
    """Map the segment's table aliases onto the aliases of the query that uses it.

    Joins are matched, never merged: each combined table the segment needs must already be combined in the host
    with the same kind, table and match columns, under any alias. Returns (mapping, None) or (None, (code, message)).
    """
    base = segment.base
    if base.table != host_from.table:
        return None, ('segment_base_mismatch',
                      f"The segment '{segment.name}' is built on {base.table}, but this query starts from {host_from.table}.")
    mapping = {base.alias: host_from.alias}
    used = {host_from.alias.lower(): host_from.table, **{j.alias.lower(): j.table for j in host_joins}}
    for join in segment.joins:
        wanted = set()
        for pair in join.on:
            left = mapping.get(pair.left.table_alias)
            wanted.add((left, pair.left.column, pair.right.column))
        match = None
        if all(item[0] is not None for item in wanted):
            for candidate in host_joins:
                have = {(p.left.table_alias, p.left.column, p.right.column) for p in candidate.on}
                if candidate.kind == join.kind and candidate.table == join.table and have == wanted \
                        and all(p.right.table_alias == candidate.alias for p in candidate.on):
                    match = candidate
                    break
        if match is not None:
            mapping[join.alias] = match.alias
            continue
        if join.alias.lower() in used:
            return None, ('segment_alias_conflict',
                          f"The segment '{segment.name}' needs {join.table} combined as '{join.alias}', "
                          f"but that name is already used for another table here.")
        return None, ('segment_join_missing', f"This segment needs the {join.table} table combined first.")
    return mapping, None


def check_references(root_id: str | None, root_source: str, root_base_table: str, direct: list[str],
                     lookup: Callable[[str], ResolvedSegment | None]) -> list[SpecError]:
    """Static checks on the reference graph of a view that is being saved.

    `lookup` returns the current segment for an id, or None. `root_id` is the view being saved (None for a new
    view), so an edit that closes a loop is caught even though the stored copy has no such edge yet.
    """
    errors: list[SpecError] = []

    def err(code: str, message: str) -> None:
        item = SpecError(code, 'filters', message)
        if item not in errors:
            errors.append(item)

    def walk(ids: list[str], stack: tuple[str, ...], names: tuple[str, ...]) -> None:
        for sid in ids:
            if sid == root_id or sid in stack:
                err('segment_cycle', cycle_message(names))
                continue
            segment = lookup(sid)
            if segment is None:
                err('segment_missing', MISSING)
                continue
            if len(stack) + 1 > SEGMENT_MAX_DEPTH:
                err('segment_depth', f'Segments can use other segments at most {SEGMENT_MAX_DEPTH} levels deep.')
                continue
            if segment.source != root_source:
                err('segment_source_mismatch', f"The segment '{segment.name}' belongs to a different source.")
                continue
            if segment.base.table != root_base_table:
                err('segment_base_mismatch',
                    f"The segment '{segment.name}' is built on {segment.base.table}, but this query starts from {root_base_table}.")
                continue
            walk(references_of(segment.filters), (*stack, sid), (*names, segment.name))

    walk(direct, (), ())
    return errors


def _plain_leaves(group: FilterGroup) -> int:
    return sum(_plain_leaves(i) if isinstance(i, FilterGroup) else 0 if isinstance(i, FilterSegment) else 1 for i in group.items)


def exceeds_limits(direct: list[str], lookup: Callable[[str], ResolvedSegment | None]) -> str | None:
    """Whether a query that uses these segments directly would hit a compile-time size limit: the nesting depth,
    the number of segment expansions (each use expands again) or the filters those expansions add up to.
    Returns a short reason, or None. Missing segments and loops are other checks' business and are skipped."""
    count = {'expansions': 0, 'leaves': 0}

    def walk(ids: list[str], stack: tuple[str, ...]) -> str | None:
        for sid in ids:
            segment = lookup(sid)
            if segment is None or sid in stack:
                continue
            if len(stack) + 1 > SEGMENT_MAX_DEPTH:
                return f'segments are nested more than {SEGMENT_MAX_DEPTH} levels deep'
            count['expansions'] += 1
            if count['expansions'] > SEGMENT_MAX_EXPANSIONS:
                return f'it would use more than {SEGMENT_MAX_EXPANSIONS} segments in total'
            count['leaves'] += _plain_leaves(segment.filters)
            if count['leaves'] > SEGMENT_MAX_LEAVES:
                return f'the segments add up to more than {SEGMENT_MAX_LEAVES} filters'
            found = walk(references_of_all(segment.filters), (*stack, sid))
            if found:
                return found
        return None
    return walk(direct, ())


def references_of_all(group: FilterGroup) -> list[str]:
    """Every segment reference in a filter tree, repeats included (the compiler expands each one)."""
    found: list[str] = []
    for item in group.items:
        if isinstance(item, FilterGroup):
            found += references_of_all(item)
        elif isinstance(item, FilterSegment):
            found.append(item.segment)
    return found
