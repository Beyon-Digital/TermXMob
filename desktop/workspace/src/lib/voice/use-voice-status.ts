import {useEffect, useMemo, useRef, useState} from 'react';

import {loadVoiceStatus, onVoiceChange, type VoiceStatus} from './settings';

/** The owner's saved voice settings and engine states. Reloads when another surface changes them. */
export function useVoiceStatus(enabled: boolean, identity?:string) {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [error, setError] = useState('');
  const active = useRef(true),identityNow=useRef(identity),loadedIdentity=useRef(identity);identityNow.current=identity;
  const reload = useMemo(
    () => async () => {
      const owner=identityNow.current;
      try {
        const next = await loadVoiceStatus();
        if (active.current&&owner===identityNow.current) {
          loadedIdentity.current=owner;
          setStatus(next);
          setError('');
        }
      } catch (failure) {
        if (active.current&&owner===identityNow.current) setError(failure instanceof Error ? failure.message : String(failure));
      }
    },
    [],
  );
  useEffect(() => {
    active.current = true;
    if (enabled) void reload();
    const off = enabled ? onVoiceChange(() => void reload()) : () => {};
    return () => {
      active.current = false;
      off();
    };
  }, [enabled, identity, reload]);
  return {status:loadedIdentity.current===identity?status:null, setStatus, error, reload};
}
