import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {useAuthSession, type Auth} from './use-auth';

afterEach(cleanup);
const alice: Auth = {principal: {id: 'alice', display_name: 'Alice', scopes: []}, session_id: 's1'};

it('starts booting and signed out', () => {
  const {result} = renderHook(() => useAuthSession());
  expect(result.current).toMatchObject({auth: null, methods: null, booting: true});
});

it('mirrors the signed-in principal into authNow after commit and fences on identity changes', () => {
  const {result} = renderHook(() => useAuthSession());
  const before = result.current.fence();
  act(() => result.current.setAuth(alice));
  expect(result.current.authNow.current).toBe(alice);
  expect(before.current()).toBe(false);
  const signedIn = result.current.fence();
  expect(signedIn.current()).toBe(true);
  act(() => { result.current.authEpoch.current++; });
  expect(signedIn.current()).toBe(false);
});

it('falls back to the locked principal while the workspace is locked', () => {
  const {result} = renderHook(() => useAuthSession());
  result.current.lockInfoNow.current = {principal: {id: 'alice'}, session_id: 's1'} as never;
  expect(result.current.fence().captured).toMatchObject({principalId: 'alice', sessionId: 's1'});
});

it('keeps fence stable across renders', () => {
  const {result} = renderHook(() => useAuthSession());
  const fence = result.current.fence;
  act(() => result.current.setBooting(false));
  expect(result.current.fence).toBe(fence);
});
