import {useEffect, useRef, useState} from 'react';
import {ApiError} from '../../lib/api';
import {useGraphqlSubscription} from '../../lib/graphql-subscription';
import {HOST_STATUS_EVENTS_QUERY, SILENCE_LIMIT_S, loadHostStatus, type HostStatus} from '../../lib/host-status';
import {usePolling} from '../../lib/use-polling';

export type HostHealth = {
  status: HostStatus | null;
  /** `forbidden`: this sign-in may not read host health, so the status bar stays as it was. */
  availability: 'loading' | 'ready' | 'forbidden';
  /** The host has not answered for more than 30 seconds. The poll keeps retrying. */
  disconnected: boolean;
  /** Whole seconds since the host last answered, while it is silent. */
  silentFor: number;
};

const forbidden = (reason: unknown) => {
  if (reason instanceof ApiError) return reason.status === 403;
  return /forbidden|missing .*authority|scope|403/i.test(reason instanceof Error ? reason.message : String(reason));
};

/**
 * Host health for the status bar: the `host_status` query, refreshed by the `host_status_events` subscription and a slow
 * poll that also keeps retrying while the host is silent. After 30 seconds without any answer the host counts as
 * disconnected. A sign-in without `machine-view` (or `host-admin` for a managed user) gets `forbidden` and no more requests.
 */
export function useHostStatus(enabled: boolean): HostHealth {
  const [status, setStatus] = useState<HostStatus | null>(null);
  const [availability, setAvailability] = useState<HostHealth['availability']>('loading');
  const [silentFor, setSilentFor] = useState(0);
  const lastAnswer = useRef(Date.now());

  const answered = (value: HostStatus) => {
    lastAnswer.current = Date.now();
    setSilentFor(0);
    setStatus(value);
    setAvailability('ready');
  };

  useGraphqlSubscription<HostStatus>(
    {
      query: HOST_STATUS_EVENTS_QUERY,
      field: 'host_status_events',
      onEvent: answered,
      onRejected: message => {
        if (forbidden(new Error(message))) setAvailability('forbidden');
      },
    },
    enabled && availability !== 'forbidden',
  );

  // The poll stays at 10 seconds even while the subscription is live: the host pushes about every 30 seconds, which is the very
  // limit for calling it disconnected, so the poll is what keeps a healthy host from looking silent.
  usePolling(() => loadHostStatus(), {
    interval: 10000,
    backoff: false,
    enabled: enabled && availability !== 'forbidden',
    onResult: answered,
    onError: reason => {
      if (forbidden(reason)) setAvailability('forbidden');
    },
  });

  // Count the silence, once a second only while the host is not answering.
  useEffect(() => {
    if (!enabled || availability !== 'ready') return;
    const timer = setInterval(() => {
      // Nothing is polled while the page is hidden, so silence then says nothing about the host.
      if (document.hidden) lastAnswer.current = Date.now();
      const quiet = Math.floor((Date.now() - lastAnswer.current) / 1000);
      setSilentFor(quiet > 5 ? quiet : 0);
    }, 1000);
    return () => clearInterval(timer);
  }, [enabled, availability]);

  return {status, availability, disconnected: availability === 'ready' && silentFor > SILENCE_LIMIT_S, silentFor};
}
