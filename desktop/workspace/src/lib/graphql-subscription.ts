import {useEffect, useRef} from 'react';
import {nativeWorkspace} from './native';

/**
 * One GraphQL subscription over graphql-transport-ws, for the agent surfaces (saved sites, CLI sessions, host health).
 *
 * The caller always keeps a REST or query fallback, because the native shell talks to the host through a bridge and has no
 * socket, and because a subscription can be refused (a missing scope). `onLive` tells the caller whether events are
 * arriving, so it can slow its own polling while they are.
 */

export interface SubscriptionOptions<T> {
  /** The full subscription document. */
  query: string;
  /** The root field of the subscription, which carries each event. */
  field: string;
  onEvent: (event: T) => void;
  /** True once the server has delivered something, false whenever the socket is not receiving events. */
  onLive?: (live: boolean) => void;
  /** The server refused the subscription (unknown scope, forbidden). Retrying cannot help, so the socket stays closed. */
  onRejected?: (message: string) => void;
}

const MAX_DELAY = 15000;

type Frame = {type?: string; id?: string; payload?: {data?: Record<string, unknown>; errors?: {message?: string}[]} | {message?: string}[]};

/** Open the subscription and keep it open until the returned function is called. Reconnects with backoff. */
export function openSubscription<T>(options: SubscriptionOptions<T>): () => void {
  if (typeof WebSocket === 'undefined' || nativeWorkspace()) return () => {};
  let stopped = false;
  let socket: WebSocket | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  let live = false;
  let rejected = false;
  const setLive = (value: boolean) => {
    if (live === value) return;
    live = value;
    options.onLive?.(value);
  };

  const connect = () => {
    if (stopped || document.hidden) return;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let current: WebSocket;
    try {
      current = new WebSocket(protocol + '//' + location.host + '/graphql', 'graphql-transport-ws');
    } catch {
      return;
    }
    socket = current;
    current.onopen = () => current.send(JSON.stringify({type: 'connection_init', payload: {}}));
    current.onmessage = message => {
      let frame: Frame;
      try {
        frame = JSON.parse(String(message.data));
      } catch {
        return;
      }
      if (frame.type === 'connection_ack') {
        current.send(JSON.stringify({id: '1', type: 'subscribe', payload: {query: options.query}}));
      } else if (frame.type === 'ping') {
        current.send(JSON.stringify({type: 'pong'}));
      } else if (frame.type === 'next') {
        const payload = frame.payload as {data?: Record<string, unknown>; errors?: {message?: string}[]} | undefined;
        if (payload?.errors?.length) {
          rejected = true;
          options.onRejected?.(payload.errors.map(error => error.message ?? '').join(' '));
          current.close();
          return;
        }
        const event = payload?.data?.[options.field] as T | undefined;
        if (event === undefined || event === null) return;
        attempt = 0;
        setLive(true);
        options.onEvent(event);
      } else if (frame.type === 'error') {
        rejected = true;
        const list = Array.isArray(frame.payload) ? frame.payload : [];
        options.onRejected?.(list.map(error => error.message ?? '').join(' '));
        current.close();
      } else if (frame.type === 'complete') {
        current.close();
      }
    };
    current.onclose = () => {
      if (socket === current) socket = undefined;
      setLive(false);
      if (stopped || rejected || document.hidden) return;
      timer = setTimeout(connect, Math.min(1000 * 2 ** attempt++, MAX_DELAY));
    };
    current.onerror = () => current.close();
  };

  const onVisibility = () => {
    if (document.hidden) {
      clearTimeout(timer);
      socket?.close();
    } else if (!socket && !rejected && !stopped) {
      clearTimeout(timer);
      attempt = 0;
      connect();
    }
  };

  document.addEventListener('visibilitychange', onVisibility);
  connect();
  return () => {
    stopped = true;
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', onVisibility);
    const closing = socket;
    socket = undefined;
    closing?.close();
  };
}

/** The same, for a component: opens while `enabled` and closes on unmount. Callbacks may change without reconnecting. */
export function useGraphqlSubscription<T>(options: SubscriptionOptions<T>, enabled: boolean) {
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });
  useEffect(() => {
    if (!enabled) return;
    return openSubscription<T>({
      query: options.query,
      field: options.field,
      onEvent: event => latest.current.onEvent(event),
      onLive: live => latest.current.onLive?.(live),
      onRejected: message => latest.current.onRejected?.(message),
    });
  }, [enabled, options.query, options.field]);
}
