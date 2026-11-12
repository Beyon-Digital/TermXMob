import {useRef, type ReactNode} from 'react';
import {useVirtualizer} from '@tanstack/react-virtual';
import {ArrowDown, ArrowUp, ChevronsUpDown} from 'lucide-react';
import {ROW_HEIGHT, alignFor, columnWidth, formatCell} from '../../lib/data-grid';
import type {ValueKind} from '../../lib/query-spec';

export type GridColumn = {key: string; label: string; kind?: ValueKind; sortable?: boolean; sort?: 'asc' | 'desc'};
type Props = {
  columns: GridColumn[]; rows: unknown[][]; /** Skeleton rows at the final column layout, kept while the data is on its way. */ loading?: boolean;
  onSort?: (index: number, additive: boolean) => void;
  /** Returns the filter popover (trigger included) for a header. */ renderFilter?: (index: number, column: GridColumn) => ReactNode;
  /** One-based number of the first row, for assistive technology. */ firstRow?: number; label?: string;
};
const SKELETON_ROWS = 8;

/** Virtualized results table. Rows are 40px; only the visible rows are in the document. */
export default function QueryGrid({columns, rows, loading = false, onSort, renderFilter, firstRow = 1, label = 'Result table'}: Props) {
  const scroller = useRef<HTMLDivElement>(null), widths = columns.map(column => columnWidth(column.label, column.kind));
  const template = widths.map(width => width + 'px').join(' '), total = widths.reduce((sum, width) => sum + width, 0);
  const count = loading && !rows.length ? SKELETON_ROWS : rows.length;
  const virtual = useVirtualizer({count, getScrollElement: () => scroller.current, estimateSize: () => ROW_HEIGHT, overscan: 8, initialRect: {width: 800, height: 400}});
  const style = {'--grid-columns': template, '--grid-width': total + 'px'} as React.CSSProperties;
  return <div ref={scroller} className="data-grid-scroll" role="region" aria-label={label} tabIndex={0}>
    <div className="data-grid" role="table" aria-label={label} aria-rowcount={rows.length + 1} aria-colcount={columns.length} style={style}>
      <div className="data-grid-head" role="rowgroup"><div className="data-grid-row" role="row" aria-rowindex={1}>
        {columns.map((column, index) => <div className="data-grid-cell data-grid-header" role="columnheader" key={column.key + index} data-align={alignFor(column.kind)}
          aria-sort={column.sort ? (column.sort === 'asc' ? 'ascending' : 'descending') : column.sortable ? 'none' : undefined}>
          {column.sortable && onSort
            ? <button type="button" className="data-grid-sort" onClick={event => onSort(index, event.shiftKey)}><span className="data-grid-label">{column.label}</span>
              {column.sort === 'asc' ? <ArrowUp size={16} strokeWidth={1.5} aria-hidden="true"/> : column.sort === 'desc' ? <ArrowDown size={16} strokeWidth={1.5} aria-hidden="true"/> : <ChevronsUpDown size={16} strokeWidth={1.5} aria-hidden="true"/>}</button>
            : <span className="data-grid-label">{column.label}</span>}
          {renderFilter?.(index, column)}
        </div>)}
      </div></div>
      <div className="data-grid-body" role="rowgroup" style={{height: virtual.getTotalSize()}}>
        {virtual.getVirtualItems().map(item => {
          const row = rows[item.index];
          return <div className="data-grid-row" role="row" key={item.key} aria-rowindex={item.index + 2} data-row={firstRow + item.index} style={{transform: 'translateY(' + item.start + 'px)'}}>
            {columns.map((column, at) => {
              if (!row) return <div className="data-grid-cell" role="cell" key={at} aria-hidden="true"><span className="data-skeleton-text"/></div>;
              const cell = formatCell(row[at], column.kind);
              return <div className="data-grid-cell" role="cell" key={at} data-align={alignFor(column.kind)} data-empty={cell.empty || undefined} title={cell.empty ? undefined : cell.text}>{cell.text}</div>;
            })}
          </div>;
        })}
      </div>
    </div>
  </div>;
}
