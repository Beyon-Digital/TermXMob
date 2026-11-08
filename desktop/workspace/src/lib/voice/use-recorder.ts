import {useCallback, useEffect, useMemo, useRef, useState, type RefObject} from 'react';
import {VoiceRecorder, type RecorderPhase} from './recorder';

export interface UseVoiceRecorder {
  /** False cancels any recording or pending permission and refuses new ones. */
  enabled: boolean;
  /** An element whose size going to zero (a hidden dock pane) cancels the recording. */
  observe?: RefObject<Element | null>;
  onRecording: (blob: Blob) => void;
  onError: (message: string) => void;
}

/**
 * The recorder plus the rules every surface needs: cancel when the page is hidden, the workspace locks, the
 * surface is disabled or its pane collapses, and when the component unmounts.
 */
export function useVoiceRecorder({enabled, observe, onRecording, onError}: UseVoiceRecorder) {
  const [phase, setPhase] = useState<RecorderPhase>('idle');
  const [level, setLevel] = useState(0);
  const enabledNow = useRef(enabled);
  enabledNow.current = enabled;
  const rendered = useRef(true);
  const callbacks = useRef({onRecording, onError});
  callbacks.current = {onRecording, onError};

  const recorder = useMemo(
    () =>
      new VoiceRecorder({
        allowed: () => enabledNow.current && rendered.current && !document.hidden,
        onPhase: setPhase,
        onLevel: setLevel,
        onRecording: blob => callbacks.current.onRecording(blob),
        onError: message => callbacks.current.onError(message),
      }),
    [],
  );

  useEffect(() => {
    if (!enabled) recorder.cancel();
  }, [enabled, recorder]);

  useEffect(() => {
    const element = observe?.current;
    const observer =
      element && typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(entries => {
            const box = entries[0]?.contentRect;
            rendered.current = !!box && box.width > 0 && box.height > 0;
            if (!rendered.current) recorder.cancel();
          })
        : null;
    if (element) observer?.observe(element);
    const hidden = () => {
      if (document.hidden) recorder.cancel();
    };
    const locked = () => recorder.cancel();
    document.addEventListener('visibilitychange', hidden);
    window.addEventListener('termx-locked', locked);
    return () => {
      document.removeEventListener('visibilitychange', hidden);
      window.removeEventListener('termx-locked', locked);
      observer?.disconnect();
      recorder.cancel();
    };
  }, [observe, recorder]);

  const start = useCallback(() => recorder.start(), [recorder]);
  const stop = useCallback(() => recorder.stop(), [recorder]);
  const cancel = useCallback(() => recorder.cancel(), [recorder]);
  return {phase, level, start, stop, cancel};
}
