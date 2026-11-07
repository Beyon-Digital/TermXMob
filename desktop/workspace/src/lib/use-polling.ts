import {useEffect, useRef} from 'react';

export interface PollingOptions<T> {
  /** Base delay between requests, in milliseconds. */
  interval: number;
  /** Pause without unmounting, for example while no operation is running. Defaults to true. */
  enabled?: boolean;
  /** Double the delay (up to `maxInterval`) while consecutive results are equal or a request fails. Defaults to true. */
  backoff?: boolean;
  /** Upper bound for the backed-off delay. Defaults to eight times `interval`. */
  maxInterval?: number;
  /** Changing this value restarts the loop with a fresh request, for example when the polled resource changes. */
  resetKey?: unknown;
  /** Fetch as soon as the loop starts. Set false when the caller has just loaded the data itself. Defaults to true. */
  immediate?: boolean;
  onResult?: (value: T) => void;
  onError?: (error: unknown) => void;
  /** Decides whether two consecutive results count as "no change". Defaults to a structural (JSON) comparison. */
  isEqual?: (previous: T, next: T) => boolean;
}

/**
 * Calls `fetcher` on a timer. Each request gets an AbortSignal that fires on unmount, when the
 * options change, and when the tab is hidden. Nothing runs while `document.hidden`; the loop
 * restarts with a fresh request as soon as the tab is visible again.
 */
function sameValue(previous: unknown, next: unknown) {
  if (Object.is(previous, next)) return true;
  try { return JSON.stringify(previous) === JSON.stringify(next); } catch { return false; }
}

export function usePolling<T>(fetcher: (signal: AbortSignal) => Promise<T>, options: PollingOptions<T>) {
  const {interval, enabled = true, backoff = true, resetKey, immediate = true} = options;
  const maxInterval = Math.max(options.maxInterval ?? interval * 8, interval);
  const latest = useRef({fetcher, onResult: options.onResult, onError: options.onError, isEqual: options.isEqual});
  useEffect(() => {
    latest.current = {fetcher, onResult: options.onResult, onError: options.onError, isEqual: options.isEqual};
  });

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let previous: T | undefined;
    let hasPrevious = false;
    let delay = interval;

    // A throwing consumer callback is a consumer bug: log it, but keep polling and keep the delay.
    const report = (callback: () => void) => { try { callback(); } catch (error) { console.error(error); } };
    const schedule = () => { if (!stopped) timer = setTimeout(() => void run(), delay); };
    const slower = () => Math.min(delay * 2, maxInterval);

    async function run() {
      if (stopped || document.hidden) return;
      const current = new AbortController();
      controller = current;
      let value: T;
      try {
        value = await latest.current.fetcher(current.signal);
      } catch (error) {
        if (stopped || current.signal.aborted) return;
        delay = backoff ? slower() : interval;
        report(() => latest.current.onError?.(error));
        schedule();
        return;
      }
      if (stopped || current.signal.aborted) return;
      const equal = latest.current.isEqual ?? sameValue;
      const unchanged = hasPrevious && equal(previous as T, value);
      previous = value;
      hasPrevious = true;
      delay = backoff && unchanged ? slower() : interval;
      report(() => latest.current.onResult?.(value));
      schedule();
    }

    const onVisibility = () => {
      clearTimeout(timer);
      controller?.abort();
      if (document.hidden) return;
      delay = interval;
      void run();
    };

    document.addEventListener('visibilitychange', onVisibility);
    if (immediate) void run(); else schedule();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [interval, enabled, backoff, maxInterval, resetKey, immediate]);
}
