import {describe, expect, it} from 'vitest';
import {
  applyCliEvent,
  attentionCount,
  canResume,
  isEnded,
  needsPerson,
  orderSessions,
  resumableCount,
  sessionName,
  sessionPlace,
  statusPresentation,
  type CliSession,
} from './cli-sessions';

const session = (over: Partial<CliSession>): CliSession => ({
  id: 's',
  cli: 'claude',
  label: 'Claude Code',
  title: null,
  status: 'working',
  status_source: 'hook',
  status_detail: null,
  status_updated_at: 1,
  attention: false,
  project_id: 'p',
  cwd: '/work/repo',
  isolation: 'none',
  worktree: null,
  pty_session_id: 'pty',
  mode: 'default',
  started_at: 1,
  updated_at: 1,
  ended_at: null,
  exit_code: null,
  ended_reason: null,
  resumable: false,
  resumed_from: null,
  resumed_to: null,
  ...over,
});

describe('CLI session status', () => {
  it('gives every documented status words and a tone', () => {
    const tones = Object.fromEntries(['starting', 'working', 'waiting_input', 'needs_approval', 'idle', 'done', 'failed', 'lost'].map(status => [status, statusPresentation(status)]));
    expect(tones.working).toEqual({label: 'Working', tone: 'busy'});
    expect(tones.waiting_input).toEqual({label: 'Waiting for input', tone: 'quiet'});
    expect(tones.needs_approval).toEqual({label: 'Needs approval', tone: 'attention'});
    expect(tones.done).toEqual({label: 'Done', tone: 'ok'});
    expect(tones.failed.tone).toBe('danger');
    expect(tones.lost.tone).toBe('danger');
  });

  it('shows a status from a newer host as written, quietly', () => {
    expect(statusPresentation('paused_for_review')).toEqual({label: 'paused for review', tone: 'quiet'});
  });

  it('knows which sessions ended, need a person and can resume', () => {
    expect(isEnded(session({status: 'lost'}))).toBe(true);
    expect(isEnded(session({status: 'working'}))).toBe(false);
    expect(needsPerson(session({status: 'waiting_input', attention: true}))).toBe(true);
    expect(needsPerson(session({status: 'done', attention: true}))).toBe(false);
    expect(canResume(session({status: 'lost', resumable: true}))).toBe(true);
    expect(canResume(session({status: 'lost', resumable: true, resumed_to: 'next'}))).toBe(false);
    expect(canResume(session({status: 'working', resumable: true}))).toBe(false);
  });
});

describe('ordering and counts', () => {
  it('puts approvals first, then sessions waiting for input, then the rest, and ended ones last', () => {
    const rows = [
      session({id: 'done', status: 'done', updated_at: 90}),
      session({id: 'working', status: 'working', updated_at: 80}),
      session({id: 'waiting', status: 'waiting_input', attention: true, updated_at: 70}),
      session({id: 'approval', status: 'needs_approval', attention: true, updated_at: 10}),
      session({id: 'lost', status: 'lost', attention: false, updated_at: 95}),
      session({id: 'working-newer', status: 'working', updated_at: 85}),
    ];
    expect(orderSessions(rows).map(row => row.id)).toEqual(['approval', 'waiting', 'working-newer', 'working', 'lost', 'done']);
  });

  it('counts the sessions that need the owner, and the lost ones that can resume', () => {
    const rows = [
      session({id: 'a', status: 'needs_approval', attention: true}),
      session({id: 'b', status: 'waiting_input', attention: true}),
      session({id: 'c', status: 'working'}),
      session({id: 'd', status: 'lost', resumable: true}),
      session({id: 'e', status: 'done', attention: true}),
    ];
    expect(attentionCount(rows)).toBe(2);
    expect(resumableCount(rows)).toBe(1);
  });

  it('names a session by its title, else its label, else the CLI', () => {
    expect(sessionName(session({title: 'Claude Code · repo'}))).toBe('Claude Code · repo');
    expect(sessionName(session({title: null}))).toBe('Claude Code');
    expect(sessionName(session({title: null, label: null}))).toBe('claude');
  });

  it('shows the worktree branch, else the project name, else the last folder', () => {
    expect(sessionPlace(session({worktree: {branch: 'termx/cli-1'}}), 'repo')).toBe('termx/cli-1');
    expect(sessionPlace(session({cwd: '/Users/me/work/8ef1/8310'}), 'repo')).toBe('repo');
    expect(sessionPlace(session({cwd: '/Users/me/work/repo'}))).toBe('repo');
    expect(sessionPlace(session({cwd: '/agents/8310db691f8d4694aa89a8bba13fe2e9'}))).toBe('');
  });
});

describe('subscription events', () => {
  it('adds a created session and updates a changed one in place', () => {
    const first = applyCliEvent([], {type: 'cli.session.created', session: session({id: 'a'})});
    expect(first.map(row => row.id)).toEqual(['a']);
    const moved = applyCliEvent(first, {type: 'cli.session.status', session: session({id: 'a', status: 'needs_approval', attention: true})});
    expect(moved).toHaveLength(1);
    expect(moved[0].status).toBe('needs_approval');
    expect(applyCliEvent(moved, {type: 'cli.session.exited', session: null})).toBe(moved);
  });
});
