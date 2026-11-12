import type {ValueKind} from './query-spec';

export const ROW_HEIGHT = 40;
export type CellAlign = 'start' | 'end';
export type CellText = {text: string; empty: boolean};

const NUMERIC: ValueKind[] = ['integer', 'number'];
export const alignFor = (kind?: ValueKind): CellAlign => kind && NUMERIC.includes(kind) ? 'end' : 'start';

/** Guess a kind from result values when the source did not describe the column (saved SQL results). */
export function guessKind(type: string | undefined, values: unknown[]): ValueKind {
  const label = (type || '').toLowerCase();
  if (/int|serial/.test(label)) return 'integer';
  if (/real|float|double|numeric|decimal|number|money/.test(label)) return 'number';
  if (/bool/.test(label)) return 'boolean';
  if (/datetime|timestamp/.test(label)) return 'datetime';
  if (/date/.test(label)) return 'date';
  const sample = values.find(value => value !== null && value !== undefined);
  if (typeof sample === 'number') return Number.isInteger(sample) ? 'integer' : 'number';
  if (typeof sample === 'boolean') return 'boolean';
  if (sample && typeof sample === 'object') return 'json';
  return 'text';
}

/** Empty values render as muted "empty" text, never the string NULL. */
export function formatCell(value: unknown, kind?: ValueKind): CellText {
  if (value === null || value === undefined || value === '') return {text: 'empty', empty: true};
  if (kind === 'boolean' && (value === 0 || value === 1 || typeof value === 'boolean')) return {text: value ? 'Yes' : 'No', empty: false};
  if (typeof value === 'object') return {text: JSON.stringify(value), empty: false};
  return {text: String(value), empty: false};
}

/** Width in px from the kind and the header text. Columns are not user-resizable. */
export function columnWidth(label: string, kind?: ValueKind): number {
  const base = kind === 'boolean' ? 96 : kind && NUMERIC.includes(kind) ? 128 : kind === 'date' || kind === 'datetime' ? 176 : 208;
  return Math.min(320, Math.max(base, label.length * 8 + 72));
}
