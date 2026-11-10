import {useCallback, useLayoutEffect, useRef, useState} from 'react';
import type {LockState} from './api';
import {sessionFence} from './session-fence';

export type Auth = {principal: {id: string; display_name: string; scopes: string[]}; session_id: string; host_id?: string};
export type Methods = {configured: boolean; passcode_required?: boolean; methods: {id: string; label?: string; flow?: string}[]};

/**
 * Who is signed in, and a fence to detect that it changed mid-request. `authNow` and `lockInfoNow` mirror
 * the latest state for async callbacks; they are updated after commit, never while rendering.
 */
export function useAuthSession() {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [methods, setMethods] = useState<Methods | null>(null);
  const [booting, setBooting] = useState(true);
  const authEpoch = useRef(0), authNow = useRef<Auth | null>(auth), lockInfoNow = useRef<LockState | null>(null);
  useLayoutEffect(() => { authNow.current = auth; });
  const fence = useCallback(() => sessionFence(() => ({
    generation: authEpoch.current,
    principalId: authNow.current?.principal.id || lockInfoNow.current?.principal.id || null,
    sessionId: authNow.current?.session_id || lockInfoNow.current?.session_id || null,
  })), []);
  return {auth, setAuth, methods, setMethods, booting, setBooting, authEpoch, authNow, lockInfoNow, fence};
}
