import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {openSubscription} from './graphql-subscription';

class FakeSocket {
  static all: FakeSocket[] = [];
  sent: unknown[] = [];
  onopen?: () => void;
  onmessage?: (event: {data: string}) => void;
  onclose?: () => void;
  onerror?: () => void;
  closed = false;
  constructor(
    public url: string,
    public protocol: string,
  ) {
    FakeSocket.all.push(this);
  }
  send(text: string) {
    this.sent.push(JSON.parse(text));
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  deliver(frame: unknown) {
    this.onmessage?.({data: JSON.stringify(frame)});
  }
}

beforeEach(() => {
  FakeSocket.all = [];
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const options = (over: Partial<Parameters<typeof openSubscription<{id: string}>>[0]> = {}) => ({
  query: 'subscription{thing{id}}',
  field: 'thing',
  onEvent: vi.fn(),
  onLive: vi.fn(),
  onRejected: vi.fn(),
  ...over,
});

describe('openSubscription', () => {
  it('speaks graphql-transport-ws: init, subscribe on ack, deliver events, answer pings', () => {
    const o = options();
    openSubscription(o);
    const socket = FakeSocket.all[0];
    expect(socket.protocol).toBe('graphql-transport-ws');
    expect(socket.url).toMatch(/^wss?:\/\/.*\/graphql$/);
    socket.onopen?.();
    expect(socket.sent[0]).toEqual({type: 'connection_init', payload: {}});
    socket.deliver({type: 'connection_ack'});
    expect(socket.sent[1]).toEqual({id: '1', type: 'subscribe', payload: {query: 'subscription{thing{id}}'}});
    socket.deliver({type: 'ping'});
    expect(socket.sent[2]).toEqual({type: 'pong'});
    socket.deliver({id: '1', type: 'next', payload: {data: {thing: {id: 'a'}}}});
    expect(o.onEvent).toHaveBeenCalledWith({id: 'a'});
    expect(o.onLive).toHaveBeenLastCalledWith(true);
  });

  it('is live only once an event has arrived, and not live again when the socket closes', () => {
    const o = options();
    openSubscription(o);
    const socket = FakeSocket.all[0];
    socket.onopen?.();
    socket.deliver({type: 'connection_ack'});
    expect(o.onLive).not.toHaveBeenCalled();
    socket.deliver({id: '1', type: 'next', payload: {data: {thing: {id: 'a'}}}});
    socket.onclose?.();
    expect(o.onLive).toHaveBeenLastCalledWith(false);
  });

  it('reconnects with backoff after a drop, and not after the server refuses the subscription', () => {
    const o = options();
    openSubscription(o);
    FakeSocket.all[0].onclose?.();
    expect(FakeSocket.all).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1].onopen?.();
    FakeSocket.all[1].deliver({type: 'connection_ack'});
    FakeSocket.all[1].deliver({id: '1', type: 'error', payload: [{message: 'forbidden: missing machine-view scope'}]});
    expect(o.onRejected).toHaveBeenCalledWith('forbidden: missing machine-view scope');
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(2);
  });

  it('treats a GraphQL error inside a next frame as a refusal too', () => {
    const o = options();
    openSubscription(o);
    const socket = FakeSocket.all[0];
    socket.onopen?.();
    socket.deliver({type: 'connection_ack'});
    socket.deliver({id: '1', type: 'next', payload: {errors: [{message: 'host-admin required'}]}});
    expect(o.onRejected).toHaveBeenCalledWith('host-admin required');
    expect(o.onEvent).not.toHaveBeenCalled();
  });

  it('closes and stops reconnecting when the caller is done', () => {
    const stop = openSubscription(options());
    const socket = FakeSocket.all[0];
    stop();
    expect(socket.closed).toBe(true);
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it('does nothing where there is no WebSocket', () => {
    vi.stubGlobal('WebSocket', undefined);
    expect(() => openSubscription(options())()).not.toThrow();
  });
});
