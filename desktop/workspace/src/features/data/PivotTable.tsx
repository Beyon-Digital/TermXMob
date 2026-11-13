import {useRef, useState, type KeyboardEvent} from 'react';
import {formatPivotNumber, rowLabel} from '../../lib/pivot';
import {humanize} from '../../lib/query-builder';
import type {OutputColumn} from '../../lib/query-spec';

type Props = {
  columns: OutputColumn[]; rows: unknown[][]; label: string;
  /** Opens the rows behind a number. Without it the cells are read-only (viewers). */ onDrill?: (rowIndex: number, columnIndex: number) => void;
};
const isValue = (column: OutputColumn) => column.role === 'pivot_cell' || column.role === 'pivot_total';
/**
 * What a column header says. A row column that still has its default name reads like the builder ("Region", not "region"). With columns
 * on the pivot, the last number is the total across them, so it must not read as one more category.
 */
export function headerLabel(column: OutputColumn, hasColumns: boolean): string {
  if (column.role === 'pivot_row') return column.column && column.label === column.column ? humanize(column.label) : column.label;
  if (column.role === 'pivot_total' && hasColumns && !/total/i.test(column.label)) return column.fn === 'count' || column.fn === 'count_distinct' || column.fn === 'sum' ? 'Total' : column.label + ' overall';
  return column.label;
}

/**
 * Pivot results as a grid. The row labels are row headers; every number is a button-like cell that opens the rows behind it.
 * One cell is in the tab order at a time (roving tabindex): arrows move, Home/End jump along the row, Ctrl+Home/End to the corners, Enter or Space opens, Esc leaves.
 */
export default function PivotTable({columns, rows, label, onDrill}: Props) {
  const first = columns.findIndex(isValue), valueColumns = first < 0 ? [] : columns.slice(first).map((_, at) => first + at).filter(at => isValue(columns[at]));
  const [active, setActive] = useState({r: 0, c: 0}), root = useRef<HTMLDivElement>(null), hasColumns = columns.some(column => column.role === 'pivot_cell');
  const lastRow = rows.length - 1, lastCol = valueColumns.length - 1;
  const focusCell = (r: number, c: number) => {
    const next = {r: Math.max(0, Math.min(lastRow, r)), c: Math.max(0, Math.min(lastCol, c))};
    setActive(next);
    root.current?.querySelector<HTMLElement>('[data-pos="' + next.r + ':' + next.c + '"]')?.focus();
  };
  function keyDown(event: KeyboardEvent<HTMLElement>, r: number, c: number) {
    const ctrl = event.ctrlKey || event.metaKey;
    const moves: Record<string, () => void> = {
      ArrowRight: () => focusCell(r, c + 1), ArrowLeft: () => focusCell(r, c - 1), ArrowDown: () => focusCell(r + 1, c), ArrowUp: () => focusCell(r - 1, c),
      Home: () => ctrl ? focusCell(0, 0) : focusCell(r, 0), End: () => ctrl ? focusCell(lastRow, lastCol) : focusCell(r, lastCol),
      Escape: () => root.current?.focus(),
      Enter: () => onDrill?.(r, valueColumns[c]), ' ': () => onDrill?.(r, valueColumns[c]),
    };
    const move = moves[event.key];
    if (move) {event.preventDefault(); move();}
  }
  return <div className="data-pivot-scroll" ref={root} tabIndex={-1} role="region" aria-label={label + ' (scrollable)'}>
    <table className="data-pivot" role="grid" aria-label={label} aria-rowcount={rows.length + 1} aria-colcount={columns.length}>
      <thead><tr>{columns.map((column, at) => <th scope="col" key={at} className="data-pivot-head" data-kind={column.role} data-empty={column.column_empty || undefined} title={column.label}>
        <span>{headerLabel(column, hasColumns)}</span></th>)}</tr></thead>
      <tbody>{rows.map((row, r) => <tr key={r}>{columns.map((column, at) => {
        const value = row[at];
        if (!isValue(column)) return <th scope="row" key={at} className="data-pivot-rowhead" data-empty={value === null || value === '' || undefined}>{rowLabel(value)}</th>;
        const c = valueColumns.indexOf(at), none = value === null || value === undefined;
        const props = {'data-pos': r + ':' + c, tabIndex: active.r === r && active.c === c ? 0 : -1, onFocus: () => setActive({r, c}), onKeyDown: (event: KeyboardEvent<HTMLElement>) => keyDown(event, r, c)};
        return <td key={at} role="gridcell" className="data-pivot-cell" data-total={column.role === 'pivot_total' || undefined} data-none={none || undefined} aria-label={none ? 'No data' : undefined}
          data-drill={onDrill ? true : undefined} {...props} onClick={onDrill ? () => onDrill(r, at) : undefined}>{none ? '–' : formatPivotNumber(value)}</td>;
      })}</tr>)}</tbody>
    </table>
  </div>;
}
