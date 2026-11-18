/* Dev-only QA harness: renders Data Studio against an in-memory stand-in for the data API.
   Served by `pnpm dev` at /qa-harness.html; the production build only bundles index.html, so it never ships.
   Recovered from a failed session (branch worktree-wf_18f50d7b-b6e-3). */
import './lib/random-uuid';
import React from 'react';
import {createRoot} from 'react-dom/client';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import './styles.css';
import './workspace-motion.css';
import './workspace-shell.css';
import './agent-theme.css';
import DataStudio from './features/data/DataStudio';

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get('theme') === 'dark' ? 'dark' : 'light';
const role = params.get('role') || 'owner';
const slow = Number(params.get('slow') || 0);

type Row = {id: number; region: string | null; status: string; amount: number | null; day: string};
const regions = ['East', 'West', 'North', null];
const rows: Row[] = Array.from({length: 96}, (_, index) => ({id: index + 1, region: regions[index % 4], status: index % 5 === 0 ? 'refunded' : 'paid', amount: index % 11 === 0 ? null : (index * 137) % 9000 + 100,
  day: '2026-0' + (1 + index % 3) + '-' + String(1 + (index * 7) % 28).padStart(2, '0')}));
const person = {id: 'p1', name: 'Alice'}, other = {id: 'p2', name: 'Bob'};
const now = () => new Date().toISOString();
const spec0 = (extra: Record<string, unknown>) => ({version: 1, source: 'c1', from: {table: 'sales', alias: 'sales'}, joins: [], select: [], filters: {op: 'and', items: []}, group_by: [], aggregates: [], sort: [], limit: 200, offset: 0, ...extra});
type View = Record<string, any>;
const views: Record<string, View> = {};
const history: Record<string, any[]> = {}, runs: Record<string, any[]> = {};
function addView(id: string, kind: string, name: string, spec: unknown, extra: Record<string, unknown> = {}) {
  views[id] = {id, kind, name, description: '', connection_id: 'c1', base_table: 'sales', revision: 1, created_by: person, updated_by: person, created_at: now(), updated_at: now(), used_by_count: 0, can_edit: role !== 'viewer', can_run: true, spec, references: [], used_by: [], ...extra};
  history[id] = [{revision: 1, action: 'created', at: now(), actor: person, name, changes: []}]; runs[id] = [];
}
addView('s1', 'segment', 'Big sales', spec0({filters: {op: 'and', items: [{field: {table_alias: 'sales', column: 'amount'}, op: 'gte', value: 5000}]}}), {used_by_count: 1});
history.s1.unshift({revision: 2, action: 'updated', at: now(), actor: other, name: 'Big sales', changes: [{field: 'filters', op: 'removed', before: {field: {table_alias: 'sales', column: 'amount'}, op: 'gte', value: 2000}}, {field: 'filters', op: 'added', after: {field: {table_alias: 'sales', column: 'amount'}, op: 'gte', value: 5000}}]});
views.s1.revision = 2;
addView('p1', 'pivot', 'Sales by region and month', spec0({pivot: {rows: [{table_alias: 'sales', column: 'region'}], columns: {table_alias: 'sales', column: 'day', bucket: 'month'}, value: {fn: 'sum', table_alias: 'sales', column: 'amount', label: 'Total amount'}, totals: true, sort: {by: 'rows', direction: 'asc'}}, filters: {op: 'and', items: [{segment: 's1', negate: false}]}}), {references: [{id: 's1', name: 'Big sales', state: 'ok'}]});

const bucket = (day: string, b: string) => b === 'year' ? day.slice(0, 4) : b === 'month' ? day.slice(0, 7) : b === 'quarter' ? day.slice(0, 4) + '-Q' + (Math.floor((+day.slice(5, 7) - 1) / 3) + 1) : day;
const value = (row: Row, dim: any) => { const raw = (row as any)[dim.column]; return dim.bucket && raw ? bucket(raw, dim.bucket) : raw; };
function leaf(row: Row, node: any): boolean {
  if ('segment' in node) { const inside = matches(row, views[node.segment]?.spec.filters); return node.negate ? !inside : inside; }
  if (node.items) return node.op === 'or' ? node.items.some((item: any) => leaf(row, item)) : node.items.every((item: any) => leaf(row, item));
  const v = (row as any)[node.field.column];
  switch (node.op) {
    case 'eq': return v === node.value; case 'ne': return v !== node.value; case 'gt': return v !== null && v > node.value; case 'gte': return v !== null && v >= node.value;
    case 'lt': return v !== null && v < node.value; case 'lte': return v !== null && v <= node.value; case 'is_null': return v === null; case 'not_null': return v !== null;
    case 'contains': return String(v ?? '').toLowerCase().includes(String(node.value).toLowerCase()); default: return true;
  }
}
const matches = (row: Row, filters: any) => !filters || leaf(row, filters);
const agg = (fn: string, list: Row[]) => { const nums = list.map(r => r.amount).filter((n): n is number => n !== null);
  if (fn === 'count') return list.length; if (fn === 'count_distinct') return new Set(nums).size; if (!nums.length) return null;
  return fn === 'sum' ? nums.reduce((a, b) => a + b, 0) : fn === 'avg' ? nums.reduce((a, b) => a + b, 0) / nums.length : fn === 'min' ? Math.min(...nums) : Math.max(...nums); };
const cmp = (a: unknown, b: unknown) => a === b ? 0 : a === null ? 1 : b === null ? -1 : a! < b! ? -1 : 1;

function evaluate(spec: any) {
  const list = rows.filter(row => matches(row, spec.filters));
  if (!spec.pivot) {
    const cols = ['id', 'region', 'status', 'amount', 'day'], kinds: Record<string, string> = {id: 'integer', region: 'text', status: 'text', amount: 'integer', day: 'date'};
    return {columns: cols.map(c => ({label: c[0].toUpperCase() + c.slice(1), value_kind: kinds[c], role: 'column', table_alias: 'sales', column: c})), rows: list.slice(spec.offset, spec.offset + spec.limit + 1).map(r => cols.map(c => (r as any)[c])), problem: null as any};
  }
  const p = spec.pivot;
  const colValues = p.columns ? [...new Set(list.map(r => value(r, p.columns)))] : [];
  if (colValues.length > 12) return {columns: [], rows: [], problem: {code: 'pivot_too_many_columns', path: 'pivot.columns', message: 'This column has more than 12 different values. Pivots show at most 12 columns. Group by a larger step, or filter first.'}};
  colValues.sort((a, b) => cmp(a, b));
  const label = p.value.label || (p.value.fn === 'count' ? 'Number of rows' : 'Total');
  const columns: any[] = [...p.rows.map((d: any, i: number) => ({label: d.label || d.column[0].toUpperCase() + d.column.slice(1), alias: 'pv_r' + (i + 1), value_kind: 'text', role: 'pivot_row', table_alias: d.table_alias, column: d.column})),
    ...colValues.map((v, i) => ({label: v === null ? '(empty)' : String(v), alias: 'pv_c' + (i + 1), value_kind: 'integer', role: 'pivot_cell', fn: p.value.fn, column_value: v, column_empty: v === null})),
    ...(p.totals || !p.columns ? [{label, alias: 'pv_total', value_kind: 'integer', role: 'pivot_total', fn: p.value.fn}] : [])];
  const groups = new Map<string, Row[]>();
  for (const r of list) { const key = JSON.stringify(p.rows.map((d: any) => value(r, d))); groups.set(key, [...(groups.get(key) || []), r]); }
  let out = [...groups.entries()].map(([key, g]) => { const keys = JSON.parse(key);
    return {keys, row: [...keys, ...colValues.map(v => { const part = g.filter(r => value(r, p.columns) === v); return part.length || p.value.fn !== 'count' ? agg(p.value.fn, part) : 0; }), ...(p.totals || !p.columns ? [agg(p.value.fn, g)] : [])], total: agg(p.value.fn, g)}; });
  out.sort((a, b) => p.sort.by === 'total' ? (cmp(a.total, b.total) * (p.sort.direction === 'desc' ? -1 : 1)) : cmp(a.keys[0], b.keys[0]) * (p.sort.direction === 'desc' ? -1 : 1));
  return {columns, rows: out.map(o => o.row).slice(spec.offset, spec.offset + spec.limit + 1), problem: null as any};
}
const jobs: Record<string, any> = {};
let jobCount = 0;
function job(spec: any, extra: Record<string, unknown> = {}) {
  const result = evaluate(spec), id = 'j' + ++jobCount;
  jobs[id] = {id, revision: 1, status: 'completed', connection_id: 'c1', result: {columns: [], rows: result.rows, row_count: result.rows.length, truncated: false}, ...extra, ...(extra.view_id ? {output: {columns: result.columns, warnings: []}} : {})};
  return jobs[id];
}
const tables = [
  {name: 'sales', row_estimate: rows.length, columns: [['id', 'integer'], ['region', 'text'], ['status', 'text'], ['amount', 'integer'], ['day', 'date']].map(([name, value_kind]) => ({name, type: value_kind.toUpperCase(), value_kind}))},
  {name: 'customers', row_estimate: 40, columns: [{name: 'id', type: 'INTEGER', value_kind: 'integer'}, {name: 'name', type: 'TEXT', value_kind: 'text'}]},
  {name: 'regions', row_estimate: 4, columns: [{name: 'id', type: 'INTEGER', value_kind: 'integer'}, {name: 'name', type: 'TEXT', value_kind: 'text'}]},
];
const summary = (v: View) => { const {spec, references, used_by, ...rest} = v; return rest; };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = new URL(typeof input === 'string' ? input : input instanceof Request ? input.url : String(input), location.href), path = url.pathname, method = (init?.method || 'GET').toUpperCase();
  if (!path.startsWith('/api/data')) return realFetch(input, init);
  if (slow) await new Promise(done => setTimeout(done, slow));
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  if (path === '/api/data/bootstrap') return json({workspaces: [{id: 'w1', revision: 1, name: 'Analytics', role}], engines: [{id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, capabilities: {read: true, schema: true}}], capabilities: {views: true, pivot: true, segments: true}});
  if (path === '/api/data/workspaces/w1') return json({workspace: {id: 'w1', revision: 1, name: 'Analytics', role}, members: [], connections: [{id: 'c1', revision: 1, name: 'Sales demo', engine: 'sqlite', config: {path: '/demo.sqlite'}, created_by: 'owner', shared_read: true}], queries: [], reports: [], dashboards: [], activity: []});
  const rest = path.replace('/api/data/workspaces/w1', '');
  if (rest === '/connections/c1/schema') return json({tables});
  if (rest === '/connections/c1/relations') return json({connection_id: 'c1', relations: []});
  if (rest === '/connections/c1/capabilities') return json({connection_id: 'c1', engine: 'sqlite', label: 'SQLite', supported: true, reason: null, qualified: true, requires_source_test: false, source_tested: true,
    dialect: {filter_ops: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'in', 'not_in', 'contains', 'starts_with', 'is_null', 'not_null'], date_bucket: {buckets: ['day', 'week', 'month', 'quarter', 'year']}, pivot: {max_columns: 12, max_row_dims: 2, column_kinds: ['text', 'integer', 'boolean', 'uuid', 'date', 'datetime']}}});
  if (rest === '/query-specs/compile') { const r = evaluate(body.spec); return json(r.problem ? {ok: false, errors: [r.problem]} : {ok: true, dialect: 'sqlite', sql: 'SELECT ...', parameters: {}, columns: r.columns, limit: body.spec.limit, offset: body.spec.offset, fetch_limit: body.spec.limit + 1, joins: [], warnings: [], schema_hash: 'x'}); }
  if (rest === '/query-specs/run') return json(job(body.spec));
  if (rest.startsWith('/jobs/')) return json(jobs[rest.split('/')[2]] || {detail: 'Not found'}, jobs[rest.split('/')[2]] ? 200 : 404);
  if (rest === '/views' && method === 'GET') return json({views: Object.values(views).map(summary)});
  if (rest === '/views' && method === 'POST') { const id = 'v' + (Object.keys(views).length + 1); addView(id, body.kind, body.name, body.spec); return json(views[id], 201); }
  const match = /^\/views\/([^/]+)(\/.*)?$/.exec(rest);
  if (match) {
    const v = views[match[1]], sub = match[2] || '';
    if (!v) return json({detail: 'Not found'}, 404);
    if (!sub && method === 'GET') return json(v);
    if (!sub && method === 'PATCH') {
      if (body.revision !== v.revision) return json({detail: {code: 'revision_conflict', message: 'Bob changed this view. Reload to see the latest version.', current_revision: v.revision, updated_by: other, updated_at: now()}}, 409);
      Object.assign(v, {...(body.name ? {name: body.name} : {}), ...(body.spec ? {spec: body.spec} : {}), revision: v.revision + 1, updated_by: person, updated_at: now()});
      history[v.id].unshift({revision: v.revision, action: 'updated', at: now(), actor: person, name: v.name, changes: body.name ? [{field: 'name', op: 'changed', before: 'old', after: v.name}] : [{field: 'filters', op: 'changed'}]});
      return json({...v, ...(v.kind === 'segment' ? {affected_views: v.used_by_count} : {})});
    }
    if (!sub && method === 'DELETE') { if (v.used_by_count) return json({detail: {code: 'segment_in_use', message: v.used_by_count + ' view uses this segment. Remove it from them first.', used_by: [{id: 'p1', kind: 'pivot', name: 'Sales by region and month'}]}}, 409); delete views[v.id]; return json(v); }
    if (sub === '/history') return json({view_id: v.id, revisions: history[v.id], next_before: null});
    if (sub === '/runs') return json({view_id: v.id, runs: runs[v.id], next_cursor: null});
    if (sub === '/run') {
      const j = job(body.mode === 'count' ? {...v.spec, aggregates: [], pivot: undefined, limit: 1000} : v.spec, {view_id: v.id, view_revision: v.revision, mode: body.mode || 'rows'});
      if (body.mode === 'count') { j.result.rows = [[rows.filter(r => matches(r, v.spec.filters)).length]]; j.output = {columns: [{label: 'Members', value_kind: 'integer', role: 'column'}], warnings: []}; }
      runs[v.id].unshift({job_id: j.id, actor: person, mine: true, view_revision: v.revision, mode: body.mode || 'rows', status: 'completed', started_at: now(), finished_at: now(), duration_ms: 120 + jobCount, row_count: j.result.row_count, truncated: false, error: null});
      return json(j);
    }
    if (sub === '/drill') {
      const c = body.coordinates, p = v.spec.pivot, items: any[] = [...(v.spec.filters.items || [])];
      if (p) { p.rows.forEach((d: any, i: number) => items.push(c.rows[i] === null ? {field: {table_alias: 'sales', column: d.column}, op: 'is_null'} : {field: {table_alias: 'sales', column: d.column}, op: 'eq', value: c.rows[i]}));
        if (c.column && p.columns) { const m = String(c.column.value); const [y, mo] = m.split('-').map(Number); items.push({field: {table_alias: 'sales', column: p.columns.column}, op: 'gte', value: m + '-01'}, {field: {table_alias: 'sales', column: p.columns.column}, op: 'lt', value: mo === 12 ? (y + 1) + '-01-01' : y + '-' + String(mo + 1).padStart(2, '0') + '-01'}); } }
      return json({spec: {...v.spec, pivot: undefined, filters: {op: 'and', items}}, exact: true, notes: [], job: null});
    }
    if (sub.startsWith('/runs/') && sub.endsWith('/export.csv')) return new Response('a,b\n1,2\n', {headers: {'content-type': 'text/csv'}});
  }
  return json({detail: 'Not found: ' + method + ' ' + rest}, 404);
};
createRoot(document.getElementById('root')!).render(<div style={{height: '100dvh', display: 'flex'}}><DataStudio ownerId="qa" visible navigationHost={params.get('nav') === '0' ? null : undefined}/></div>);
