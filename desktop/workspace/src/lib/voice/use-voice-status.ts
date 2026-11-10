import {useEffect, useMemo, useRef, useState} from 'react';

import {loadVoiceStatus, onVoiceChange, type VoiceStatus} from './settings';

/** The owner's saved voice settings and engine states. Reloads when another surface changes them. */
export function useVoiceStatus(enabled: boolean) {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [error, setError] = useState('');
  const active = useRef(true);
  const reload = useMemo(
    () => async () => {
      try {
        const next = await loadVoiceStatus();
        if (active.current) {
          setStatus(next);
          setError('');
        }
      } catch (failure) {
        if (active.current) setError(failure instanceof Error ? failure.message : String(failure));
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
  }, [enabled, reload]);
  return {status, setStatus, error, reload};
}
