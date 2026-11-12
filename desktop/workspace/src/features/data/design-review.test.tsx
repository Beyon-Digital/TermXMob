/**
 * Design review (independent reviewer). Each test below FAILS on the composed branch and proves a finding:
 * it encodes the DESIGN.md rule that the current implementation breaks. Do not weaken these; fix the code.
 */
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataStudio from './DataStudio';
import {request} from '../../lib/api';
import {columnWidth} from '../../lib/data-grid';
import {sizeGridScrollers} from '../../test-utils/virtual';
import '../../test-utils/codemirror';
import type {DataBootstrap} from '../../lib/data-studio';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./BIStudio', () => ({default: () => <section aria-label="BI reports"/>}));
vi.mock('./DataCopilot', () => ({default: () => <section aria-label="Copilot conversation"/>}));

const engine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, fields: [], capabilities: {read: true, schema: true, cancel: true, export_csv: true}};
const api = vi.mocked(request);
beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers(); localStorage.clear();
  window.history.replaceState({}, '', '/?workspace=data');
  api.mockImplementation(async path => {
    if (path === '/api/data/bootstrap') return {workspaces: [{id: 'w', revision: 1, name: 'Analytics', role: 'owner'}], engines: [engine], capabilities: {}} satisfies DataBootstrap;
    if (path === '/api/data/workspaces/w') return {
      workspace: {id: 'w', revision: 1, name: 'Analytics', role: 'owner'}, members: [],
      connections: [{id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {path: '/host/orders.sqlite'}, created_by: 'owner'}],
      queries: [], reports: [], dashboards: [], activity: [],
    };
    throw new Error('Unexpected request: ' + path);
  });
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('moves focus into the SQL editor panel when Advanced > SQL editor opens it', async () => {
  // Browser evidence (Chrome 1280 and 375): after Enter on the menu item, document.activeElement is the Advanced trigger and
  // `.cm-editor.cm-focused` does not exist. The panel is portalled to the end of <body>, so a keyboard user must Tab past
  // every control on the page to reach it. DESIGN.md: "Full keyboard path. Logical tab order."
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await screen.findByRole('heading', {name: /source$/});
  await user.click(screen.getByRole('button', {name: 'Advanced'}));
  await user.click(await screen.findByRole('menuitemcheckbox', {name: 'SQL editor'}));
  const panel = await screen.findByRole('dialog', {name: 'SQL editor'});
  await screen.findByLabelText('SQL query');
  await waitFor(() => expect(panel.contains(document.activeElement)).toBe(true));
});

it('sizes grid columns so a header label plus its sort icon and filter button is not clipped', () => {
  // Chrome 1280 on a real SQLite file: the "amount" header label measures scrollWidth 50 in a 48px box and
  // "customer_id" 82 in 80 (text-overflow: ellipsis renders "amo..." and "customer..."), with no tooltip on the header.
  // Non-label chrome in a header cell is 80px (a 128px cell minus the 48px its label box gets).
  const CHROME = 80;
  expect(columnWidth('amount', 'integer')).toBeGreaterThanOrEqual(50 + CHROME);
  expect(columnWidth('customer_id', 'integer')).toBeGreaterThanOrEqual(82 + CHROME);
});
