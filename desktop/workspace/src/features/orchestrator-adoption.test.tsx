import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import OrchestratorAdoption, {type AdoptionPreview} from './OrchestratorAdoption';
import {ApiError, request, type Session} from '../lib/api';

vi.mock('../lib/api', async original => ({...await original<typeof import('../lib/api')>(), request: vi.fn()}));
const api = vi.mocked(request), refresh = vi.fn();
const session = {id: 'focused-chat', title: 'Focused existing chat', engine: 'codex', cwd: '/host/project'} as Session;
const preview: AdoptionPreview = {session_id: session.id, revision: 7, review_digest: 'review-7', title: session.title, engine: 'codex', cwd: '/host/project', model: 'model-a', mode: 'agent', active_task_ids: [], control_owner: 'user', effect: 'Enroll status and reports. Control remains with the user.', enrollment: {enrolled: false, control_owner: null}};
const props = {ownerId: 'owner', session, onRefresh: refresh};
const result = {ok: true, session_id: session.id, engine: 'codex', control_owner: 'user', requires_user_handoff: true};
const posts = () => api.mock.calls.filter(([path, init]) => path === '/api/workspace/orchestrator/adopt' && init?.method === 'POST');
beforeEach(() => { window.dispatchEvent(new Event('termx-signed-out')); api.mockReset(); api.mockResolvedValue(preview); refresh.mockReset(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('reads the exact target only on demand and requires explicit enrollment confirmation', async () => {
  let enrolled = false;
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') { enrolled = true; return result; }
    return enrolled ? {...preview, enrollment: {enrolled: true, control_owner: 'user'}} : preview;
  });
  render(<OrchestratorAdoption {...props}/>);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText('/host/project');
  expect(screen.getByText('codex')).toBeVisible();
  expect(screen.getByText('Enroll status and reports. Control remains with the user.')).toBeVisible();
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', {name: 'Include this chat'}));
  await screen.findByText(/Included in main agent updates. Check this chat’s control banner/);
  expect(posts()).toHaveLength(1);
  expect(JSON.parse(String(posts()[0][1]?.body))).toMatchObject({session_id: session.id, expected_revision: 7, review_digest: 'review-7', request_id: expect.any(String)});
  expect(api.mock.calls.some(([path]) => path.includes('/control') || path.endsWith('/turns'))).toBe(false);
  expect(refresh).toHaveBeenCalledOnce();
});

it('preserves the exact idempotent payload after a failed send and panel replacement', async () => {
  let attempts = 0;
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') { attempts++; if (attempts === 1) throw new ApiError(504, 'Outcome uncertain'); return result; }
    return attempts > 1 ? {...preview, enrollment: {enrolled: true, control_owner: 'user'}} : preview;
  });
  const view = render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText('/host/project');
  fireEvent.click(screen.getByRole('button', {name: 'Include this chat'}));
  await screen.findByRole('button', {name: 'Retry same enrollment'});
  const original = posts()[0][1]?.body;
  view.unmount(); render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Retry same enrollment'}));
  await screen.findByText(/Included in main agent updates. Check this chat’s control banner/);
  expect(posts()).toHaveLength(2);
  expect(posts()[1][1]?.body).toBe(original);
});

it('requires a fresh explicit review after conflict and invalidates the old request key', async () => {
  let attempts = 0;
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') { attempts++; if (attempts === 1) throw new ApiError(409, 'Conversation changed'); return result; }
    return attempts ? {...preview, revision: 8, review_digest: 'review-8', enrollment: {enrolled: attempts > 1, control_owner: attempts > 1 ? 'user' : null}} : preview;
  });
  render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText('/host/project');
  fireEvent.click(screen.getByRole('button', {name: 'Include this chat'}));
  const latest = await screen.findByRole('button', {name: 'Review latest details'});
  expect(api.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(1);
  expect(posts()).toHaveLength(1);
  fireEvent.click(latest); await screen.findByText('/host/project');
  fireEvent.click(screen.getByRole('button', {name: 'Include this chat'}));
  await waitFor(() => expect(posts()).toHaveLength(2));
  const first = JSON.parse(String(posts()[0][1]?.body)), second = JSON.parse(String(posts()[1][1]?.body));
  expect(second).toMatchObject({expected_revision: 8, review_digest: 'review-8'});
  expect(second.request_id).not.toBe(first.request_id);
});

it('never applies a late enrollment response to another selected chat or account', async () => {
  let finish!: (value: typeof result) => void;
  api.mockImplementation(async (_path, init) => init?.method === 'POST' ? new Promise<typeof result>(resolve => { finish = resolve; }) : preview);
  const view = render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText('/host/project');
  const include = screen.getByRole('button', {name: 'Include this chat'});
  fireEvent.click(include); fireEvent.click(include);
  view.rerender(<OrchestratorAdoption {...props} ownerId="other-owner" session={{...session, id: 'other-chat'}}/>);
  await act(async () => finish(result));
  expect(posts()).toHaveLength(1);
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText(/Included in main agent updates/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Include in main agent updates'})).toBeVisible();
});

it('recognizes an already managed chat without submitting an enrollment or changing its owner', async () => {
  api.mockResolvedValue({...preview, control_owner: 'orchestrator', enrollment: {enrolled: true, control_owner: 'orchestrator'}});
  render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText(/This chat is already included. At review time, the main agent had control/);
  expect(screen.queryByRole('button', {name: 'Include this chat'})).not.toBeInTheDocument();
  expect(posts()).toHaveLength(0);
});

it('refreshes current ownership after a replayed receipt instead of asserting its original owner', async () => {
  let submitted = false;
  api.mockImplementation(async (_path, init) => {
    if (init?.method === 'POST') { submitted = true; return result; }
    return submitted ? {...preview, control_owner: 'orchestrator', enrollment: {enrolled: true, control_owner: 'orchestrator'}} : preview;
  });
  render(<OrchestratorAdoption {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Include in main agent updates'}));
  await screen.findByText('/host/project');
  fireEvent.click(screen.getByRole('button', {name: 'Include this chat'}));
  await screen.findByText(/At review time, the main agent had control/);
  expect(screen.getByText(/Included in main agent updates. Check this chat’s control banner/)).toBeVisible();
  expect(screen.queryByText(/Included in main agent updates. You are in control/)).not.toBeInTheDocument();
  expect(api.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(2);
});
