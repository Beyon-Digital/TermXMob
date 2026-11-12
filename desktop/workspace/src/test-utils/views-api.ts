import {vi} from 'vitest';
import {request} from '../lib/api';
import compile from './ui-fixtures/compile-pivot.json';
import drillCell from './ui-fixtures/drill-pivot-cell.json';
import createPivot from './ui-fixtures/view-create-pivot.json';
import createSegment from './ui-fixtures/view-create-segment.json';
import type {QuerySpec, SchemaTable} from '../lib/query-spec';
import type {ViewDetail} from '../lib/data-views';
import type {SourceController} from '../features/data/use-source';

export const salesTable: SchemaTable = {name: 'sales', row_estimate: 120, columns: [
  {name: 'id', type: 'INTEGER', primary_key: true, value_kind: 'integer'}, {name: 'region', type: 'TEXT', value_kind: 'text'}, {name: 'status', type: 'TEXT', value_kind: 'text'},
  {name: 'amount', type: 'REAL', value_kind: 'number'}, {name: 'day', type: 'DATE', value_kind: 'date'}]};
export const controller = (extra: Partial<SourceController> = {}): SourceController => ({schema: {tables: [salesTable]}, schemaStatus: 'ready', schemaError: '', needsTest: false, testing: false, testMessage: '', testError: '', loadSchema: vi.fn(), testConnection: vi.fn(), ...extra});

export const pivotColumns = compile.response.columns;
export const pivotRows: unknown[][] = [['East', 100, 250, null, 350], ['West', 40, 1500, 60, 1600]];
const person = {id: 'p1', name: 'Alice'};
export const detail = (kind: 'pivot' | 'segment', extra: Partial<ViewDetail> = {}): ViewDetail => ({
  ...(kind === 'pivot' ? createPivot.response : createSegment.response), id: 'v1', connection_id: 'c1', created_by: person, updated_by: person, created_at: '2026-11-13T09:00:00+00:00', updated_at: '2026-11-13T09:00:00+00:00',
  spec: ((kind === 'pivot' ? createPivot.request.spec : createSegment.request.spec) as unknown) as QuerySpec, references: [], used_by: [], ...extra,
} as unknown as ViewDetail);

export type Calls = {path: string; method: string; body: any}[];
export type Behaviour = {
  views: Record<string, ViewDetail>; calls: Calls; list?: ViewDetail[]; rows?: unknown[][];
  /** Throw this for a request whose path contains the key. A key like "PATCH /views/v1" also matches the method. */ fail: Record<string, unknown>;
  /** Replace the response for a request whose path ends with the key. */ reply: Record<string, unknown>;
  capabilities?: unknown;
};
export function newBehaviour(): Behaviour {return {views: {v1: detail('pivot')}, calls: [], fail: {}, reply: {}};}

/** A small in-memory stand-in for the data API: views, runs, compiles and drill-downs. Every request is recorded in `calls`. */
export function installViewApi(behaviour: Behaviour) {
  const api = vi.mocked(request);
  api.mockImplementation(async (path, init) => {
    const method = (init?.method || 'GET').toUpperCase(), body = init?.body ? JSON.parse(String(init.body)) : undefined, bare = path.split('?')[0];
    behaviour.calls.push({path, method, body});
    for (const [key, error] of Object.entries(behaviour.fail)) if (key.includes(' ') ? key.startsWith(method + ' ') && path.includes(key.slice(method.length + 1)) : path.includes(key)) throw error;
    for (const [key, value] of Object.entries(behaviour.reply)) if (bare.endsWith(key)) return value;
    const rows = behaviour.rows ?? pivotRows;
    if (bare.endsWith('/capabilities')) return behaviour.capabilities ?? {connection_id: 'c1', engine: 'sqlite', label: 'SQLite', supported: true, reason: null, qualified: true, requires_source_test: false, source_tested: true,
      dialect: {filter_ops: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'contains', 'starts_with', 'is_null', 'not_null'], date_bucket: {buckets: ['day', 'week', 'month', 'quarter', 'year']},
        pivot: {max_columns: 12, max_row_dims: 2, column_kinds: ['text', 'integer', 'boolean', 'uuid', 'date', 'datetime']}}};
    if (bare.endsWith('/query-specs/compile')) return {...compile.response, limit: 200, offset: body.spec.offset || 0};
    if (bare.endsWith('/query-specs/run')) return {id: 'live1', revision: 1, status: 'completed', connection_id: 'c1', result: {columns: [], rows, row_count: rows.length, truncated: false}};
    if (bare.endsWith('/relations')) return {connection_id: 'c1', relations: []};
    const id = /\/views\/([^/]+)/.exec(bare)?.[1];
    if (bare.endsWith('/views') && method === 'GET') return {views: behaviour.list ?? Object.values(behaviour.views)};
    if (bare.endsWith('/views') && method === 'POST') {
      const created = detail(body.kind, {id: 'new1', name: body.name, spec: body.spec}); behaviour.views.new1 = created; return created;
    }
    if (id && bare.endsWith('/run')) {
      const view = behaviour.views[id], count = body.mode === 'count';
      return {id: 'run-' + behaviour.calls.length, revision: 1, status: 'completed', connection_id: 'c1', view_id: id, view_revision: view.revision, mode: body.mode || 'rows',
        output: {columns: count ? [{label: 'Members', value_kind: 'integer', role: 'column'}] : view.kind === 'pivot' ? pivotColumns : [], warnings: []},
        result: {columns: [], rows: count ? [[42]] : rows, row_count: count ? 1 : rows.length, truncated: false}};
    }
    if (id && bare.endsWith('/drill')) return {...drillCell.response, spec: behaviour.views[id]?.kind === 'segment' ? behaviour.views[id].spec : drillCell.response.spec};
    if (id && /\/(history|runs)$/.test(bare)) return bare.endsWith('/history') ? {view_id: id, revisions: [], next_before: null} : {view_id: id, runs: [], next_cursor: null};
    if (id && method === 'GET') return behaviour.views[id];
    if (id && method === 'PATCH') {
      const view = behaviour.views[id], next = {...view, ...body, revision: view.revision + 1, spec: body.spec ?? view.spec} as ViewDetail; delete (next as any).revision_sent; behaviour.views[id] = next; return next;
    }
    if (id && method === 'DELETE') return {...behaviour.views[id], revision: behaviour.views[id].revision + 1};
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });
  return api;
}
