import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {announceSubmissionChange, draftSubmissionOwners, publishDraftHandoff, sessionDrafts, setTaskStopping, stoppingTasks, submittingDrafts} from './drafts';
import {useSessionDraft, useWhenChanged} from './use-session-draft';

afterEach(() => { cleanup(); sessionDrafts.clear(); submittingDrafts.clear(); draftSubmissionOwners.clear(); stoppingTasks.clear(); });

it('re-renders with the sending state when a submission starts and ends', () => {
  const {result} = renderHook(() => useSessionDraft('s1'));
  expect(result.current.sending).toBe(false);
  act(() => { submittingDrafts.add('s1'); announceSubmissionChange(); });
  expect(result.current.sending).toBe(true);
  act(() => { submittingDrafts.delete('s1'); announceSubmissionChange(); });
  expect(result.current.sending).toBe(false);
});

it('ignores submissions of other sessions', () => {
  const {result} = renderHook(() => useSessionDraft('s1'));
  act(() => { submittingDrafts.add('s2'); announceSubmissionChange(); });
  expect(result.current.sending).toBe(false);
});

it('delivers a draft handoff to every mounted conversation exactly once', () => {
  const first = renderHook(() => useSessionDraft('s1'));
  const second = renderHook(() => useSessionDraft('s1'));
  const draft = {text: 'from detached window', saved: '', dirty: true};
  act(() => publishDraftHandoff('s1', draft));
  for (const view of [first, second]) {
    expect(view.result.current.handoff).toMatchObject({sessionId: 's1', draft});
  }
  const seq = first.result.current.handoff!.seq;
  act(() => publishDraftHandoff('s1', {...draft, text: 'again'}));
  expect(first.result.current.handoff!.seq).toBe(seq + 1);
});

it('tracks tasks being stopped across panels', () => {
  const {result} = renderHook(() => useSessionDraft('s1'));
  expect(result.current.isStopping('t1')).toBe(false);
  act(() => setTaskStopping('t1', true));
  expect(result.current.isStopping('t1')).toBe(true);
  act(() => setTaskStopping('t1', false));
  expect(result.current.isStopping('t1')).toBe(false);
});

it('stops listening on unmount', () => {
  const render = vi.fn();
  const {unmount} = renderHook(() => { render(); return useSessionDraft('s1'); });
  unmount();
  render.mockClear();
  act(() => announceSubmissionChange());
  expect(render).not.toHaveBeenCalled();
});

it('useWhenChanged skips the value present at mount and fires for later changes only', () => {
  const effect = vi.fn();
  const {rerender} = renderHook(({value}) => useWhenChanged(value, effect), {initialProps: {value: 1}});
  expect(effect).not.toHaveBeenCalled();
  rerender({value: 1});
  expect(effect).not.toHaveBeenCalled();
  rerender({value: 2});
  expect(effect).toHaveBeenCalledWith(2);
});
