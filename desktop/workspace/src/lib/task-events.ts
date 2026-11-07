import {useEffect, useRef} from 'react';
import {nativeWorkspace} from './native';

export interface TaskStreamEvent {type: string; sequence?: number; task_id?: string; payload?: unknown}
export interface TaskEventsOptions {
  onEvent: (event: TaskStreamEvent) => void;
  /** True once the subscription is acknowledged, false whenever it is not receiving events. */
  onLive?: (live: boolean) => void;
}

const QUERY = 'subscription($taskId:String!,$after:Int!){agent_task_events(task_id:$taskId,after:$after){sequence type task_id payload created_at}}';
const MAX_DELAY = 15000;

/**
 * Follows a running task through the existing `agent_task_events` GraphQL subscription
 * (graphql-transport-ws). The caller keeps polling as a fallback and slows it only while
 * `onLive(true)` holds. The socket closes while the tab is hidden and when `taskId` is null.
 * The native shell talks to the host through its own bridge and has no socket, so it stays on polling.
 */
export function useTaskEvents(taskId: string | null, options: TaskEventsOptions) {
  const latest = useRef(options);
  useEffect(() => { latest.current = options; });

  useEffect(() => {
    if (!taskId || typeof WebSocket === 'undefined' || nativeWorkspace()) return;
    let stopped = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let after = 0;
    let live = false;
    let rejected = false;
    const setLive = (value: boolean) => { if (live !== value) { live = value; latest.current.onLive?.(value); } };

    const connect = () => {
      if (stopped || document.hidden) return;
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      let current: WebSocket;
      try { current = new WebSocket(`${protocol}//${location.host}/graphql`, 'graphql-transport-ws'); } catch { return; }
      socket = current;
      const id = '1';
      current.onopen = () => current.send(JSON.stringify({type: 'connection_init', payload: {}}));
      current.onmessage = message => {
        let frame: {type?: string; id?: string; payload?: {data?: {agent_task_events?: TaskStreamEvent}}};
        try { frame = JSON.parse(String(message.data)); } catch { return; }
        if (frame.type === 'connection_ack') {
          current.send(JSON.stringify({id, type: 'subscribe', payload: {query: QUERY, variables: {taskId, after}}}));
        } else if (frame.type === 'ping') {
          current.send(JSON.stringify({type: 'pong'}));
        } else if (frame.type === 'next') {
          const event = frame.payload?.data?.agent_task_events;
          if (!event) return;
          // Live only once the server has delivered something, so a subscription it rejects never counts as healthy.
          attempt = 0;
          setLive(true);
          if (typeof event.sequence === 'number') after = Math.max(after, event.sequence);
          if (event.type !== 'heartbeat') latest.current.onEvent(event);
        } else if (frame.type === 'error' || frame.type === 'complete') {
          // An error means the server refused the subscription (unknown task, missing scope). Retrying cannot help.
          if (frame.type === 'error') rejected = true;
          current.close();
        }
      };
      current.onclose = () => {
        if (socket === current) socket = undefined;
        setLive(false);
        if (stopped || rejected || document.hidden) return;
        const delay = Math.min(1000 * 2 ** attempt++, MAX_DELAY);
        timer = setTimeout(connect, delay);
      };
      current.onerror = () => current.close();
    };

    const onVisibility = () => {
      if (document.hidden) {
        clearTimeout(timer);
        socket?.close();
      } else if (!socket && !rejected) {
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
  }, [taskId]);
}
