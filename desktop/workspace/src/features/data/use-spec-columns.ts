import {useCallback, useMemo} from 'react';
import {humanize} from '../../lib/query-builder';
import type {ColumnRef, Join, SchemaTable, TableRef, ValueKind} from '../../lib/query-spec';
import type {ColumnOption} from './ColumnFilter';

/** Column labels, kinds and picker options for a base table plus its combined tables. Shared by tabs that build specs. */
export function useSpecColumns(from: TableRef, joins: Join[], tables: SchemaTable[]) {
  const schemaOf = useCallback((name: string) => tables.find(row => row.name === name), [tables]);
  const aliasTable = useCallback((alias: string) => alias === from.alias ? from.table : joins.find(join => join.alias === alias)?.table || alias, [from, joins]);
  const labelFor = useCallback((ref: ColumnRef) => joins.length ? humanize(aliasTable(ref.table_alias)) + ' · ' + humanize(ref.column) : humanize(ref.column), [joins.length, aliasTable]);
  const kindFor = useCallback((ref: ColumnRef): ValueKind | undefined => schemaOf(aliasTable(ref.table_alias))?.columns.find(column => column.name === ref.column)?.value_kind, [schemaOf, aliasTable]);
  const options = useMemo<ColumnOption[]>(() => [{alias: from.alias, table: from.table}, ...joins.map(join => ({alias: join.alias, table: join.table}))].flatMap(entry =>
    (schemaOf(entry.table)?.columns || []).map(column => {const ref = {table_alias: entry.alias, column: column.name}; return {ref, label: labelFor(ref), kind: column.value_kind};})), [from, joins, schemaOf, labelFor]);
  return {labelFor, kindFor, options, aliasTable, schemaOf};
}
