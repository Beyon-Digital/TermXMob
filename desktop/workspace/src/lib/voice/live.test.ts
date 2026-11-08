import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {BrowserAudio, LiveVoiceClient, type LiveAudio, type LiveHooks} from './live';

class FakeSocket {
  static last: FakeSocket;
  binaryType = 'blob';
  readyState = 0;
  sent: (string | ArrayBuffer)[] = [];
  closedWith: number | undefined;
  onopen: (() => void) | null = null;
  onmessage: ((event: {data: unknown}) => void) | null = null;
  onclose: ((event: {code: number}) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }
  close(code = 1000) {
    this.closedWith = code;
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  say(message: unknown) {
    this.onmessage?.({data: typeof message === 'string' || message instanceof ArrayBuffer ? message : JSON.stringify(message)});
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({code});
  }
  commands() {
    return this.sent.filter((item): item is string => typeof item === 'string').map(item => JSON.parse(item));
  }
  frames() {
    return this.sent.filter(item => typeof item !== 'string');
  }
}

class FakeAudio implements LiveAudio {
  onFrame: (pcm: ArrayBuffer, level: number) => void = () => {};
  played: ArrayBuffer[] = [];
  flushes = 0;
  closed = 0;
  failure: Error | null = null;
  async start(onFrame: (pcm: ArrayBuffer, level: number) => void) {
    if (this.failure) throw this.failure;
    this.onFrame = onFrame;
  }
  play(pcm: ArrayBuffer) {
    this.played.push(pcm);
    return 0.5;
  }
  flush() {
    this.flushes++;
  }
  close() {
    this.closed++;
  }
}

let audio: FakeAudio;
let hooks: {[K in keyof LiveHooks]: ReturnType<typeof vi.fn>};
let client: LiveVoiceClient;
const frame = () => new Int16Array([1, 2, 3]).buffer;

beforeEach(() => {
  audio = new FakeAudio();
  hooks = {onState: vi.fn(), onLevel: vi.fn(), onTranscript: vi.fn(), onNotice: vi.fn(), onEnded: vi.fn()};
  client = new LiveVoiceClient(hooks, {socket: url => new FakeSocket(url) as unknown as WebSocket, audio: () => audio});
});
afterEach(() => {
  vi.useRealTimers();
});

async function started() {
  await client.start('conv 1');
  FakeSocket.last.open();
  return FakeSocket.last;
}

it('opens the same-origin socket for the conversation and sends microphone frames once it is open', async () => {
  await client.start('conv 1');
  const socket = FakeSocket.last;
  expect(socket.url).toMatch(/^wss?:\/\/[^/]+\/api\/voice\/live\?conversation_id=conv%201$/);
  expect(socket.binaryType).toBe('arraybuffer');
  audio.onFrame(frame(), 0.4);
  expect(socket.frames()).toHaveLength(0);
  socket.open();
  audio.onFrame(frame(), 0.4);
  expect(socket.frames()).toHaveLength(1);
  expect(hooks.onLevel).toHaveBeenLastCalledWith(0.4);
});

it('plays host audio, shows transcripts and follows the host state', async () => {
  const socket = await started();
  socket.say({type: 'state', state: 'listening'});
  socket.say({type: 'transcript', role: 'user', text: 'run the tests'});
  const chunk = frame();
  socket.say(chunk);
  expect(hooks.onState).toHaveBeenCalledWith('listening');
  expect(hooks.onTranscript).toHaveBeenCalledWith('user', 'run the tests');
  expect(audio.played).toEqual([chunk]);
});

it('flushes playback when the host says the user spoke over the reply', async () => {
  const socket = await started();
  socket.say({type: 'flush'});
  expect(audio.flushes).toBe(1);
});

it('mute stops sending audio and tells the host; unmute resumes', async () => {
  const socket = await started();
  client.mute(true);
  audio.onFrame(frame(), 0.7);
  expect(socket.frames()).toHaveLength(0);
  expect(hooks.onLevel).toHaveBeenLastCalledWith(0);
  client.mute(false);
  audio.onFrame(frame(), 0.2);
  expect(socket.frames()).toHaveLength(1);
  expect(socket.commands()).toEqual([{type: 'mute', muted: true}, {type: 'mute', muted: false}]);
});

it('interrupt flushes local playback immediately and asks the host to cancel', async () => {
  const socket = await started();
  client.interrupt();
  expect(audio.flushes).toBe(1);
  expect(socket.commands()).toEqual([{type: 'interrupt'}]);
});

it('end stops the microphone at once and finishes when the host confirms', async () => {
  const socket = await started();
  client.end();
  expect(audio.closed).toBe(1);
  expect(socket.commands()).toEqual([{type: 'end', reason: 'user'}]);
  expect(hooks.onEnded).not.toHaveBeenCalled();
  socket.say({type: 'ended', reason: 'user', detail: 'The session ended.'});
  expect(hooks.onEnded).toHaveBeenCalledWith('user', 'The session ended.');
  expect(socket.closedWith).toBeDefined();
  expect(hooks.onState).toHaveBeenLastCalledWith('ended');
});

it('ends on its own if the host never confirms', async () => {
  vi.useFakeTimers();
  await client.start('c');
  FakeSocket.last.open();
  client.end();
  await vi.advanceTimersByTimeAsync(2000);
  expect(hooks.onEnded).toHaveBeenCalledTimes(1);
});

it('reports an ended session once, with the host sentence', async () => {
  const socket = await started();
  socket.say({type: 'ended', reason: 'idle', detail: 'The session ended after 60 seconds without speech.'});
  socket.drop(1000);
  expect(hooks.onEnded).toHaveBeenCalledTimes(1);
  expect(hooks.onEnded).toHaveBeenCalledWith('idle', 'The session ended after 60 seconds without speech.');
  expect(audio.closed).toBeGreaterThan(0);
});

it('turns a refusing close code into a next step', async () => {
  await client.start('c');
  FakeSocket.last.drop(4401);
  expect(hooks.onEnded).toHaveBeenCalledWith('connection_lost', expect.stringMatching(/sign in/i));
});

it('says what to do when the microphone is blocked and never opens a socket', async () => {
  audio.failure = new Error('denied');
  FakeSocket.last = undefined as never;
  await client.start('c');
  expect(hooks.onEnded).toHaveBeenCalledWith('microphone', expect.stringMatching(/microphone/i));
  expect(FakeSocket.last).toBeUndefined();
});

it('ends when the tab is hidden', async () => {
  const socket = await started();
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
  document.dispatchEvent(new Event('visibilitychange'));
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
  expect(socket.commands()).toEqual([{type: 'end', reason: 'hidden'}]);
  expect(audio.closed).toBe(1);
});

it('ends when the workspace locks', async () => {
  const socket = await started();
  window.dispatchEvent(new Event('termx-locked'));
  expect(socket.commands()).toEqual([{type: 'end', reason: 'locked'}]);
});

it('shows provider errors without ending the session', async () => {
  const socket = await started();
  socket.say({type: 'error', detail: 'rate limited'});
  expect(hooks.onNotice).toHaveBeenCalledWith('rate limited');
  expect(hooks.onEnded).not.toHaveBeenCalled();
});

it('ignores malformed messages', async () => {
  const socket = await started();
  socket.say('not json');
  socket.say({type: 'mystery'});
  expect(hooks.onEnded).not.toHaveBeenCalled();
});

it('does not start twice', async () => {
  await started();
  const first = FakeSocket.last;
  await client.start('other');
  expect(FakeSocket.last).toBe(first);
});

it('does not open a socket when it is ended while the permission prompt is still open', async () => {
  let allow: () => void = () => {};
  audio.start = () => new Promise<void>(resolve => (allow = resolve));
  const created = vi.fn();
  client = new LiveVoiceClient(hooks, {
    socket: url => {
      created();
      return new FakeSocket(url) as unknown as WebSocket;
    },
    audio: () => audio,
  });
  const pending = client.start('c1');
  client.end('user');
  const closedBefore = audio.closed;
  allow();
  await pending;
  expect(created).not.toHaveBeenCalled();
  expect(audio.closed).toBeGreaterThan(closedBefore);
});

it('releases a microphone that is granted after the session was closed', async () => {
  const stop = vi.fn();
  let grant: (stream: unknown) => void = () => {};
  const node = {port: {}, connect: () => ({connect: () => {}}), disconnect: () => {}};
  vi.stubGlobal('AudioWorkletNode', class { constructor() { return node as never; } });
  vi.stubGlobal('AudioContext', class {
    audioWorklet = {addModule: () => Promise.resolve()};
    destination = {};
    createGain() { return {gain: {}, connect: () => {}}; }
    createMediaStreamSource() { return {connect: () => {}}; }
    resume() { return Promise.resolve(); }
    close() { return Promise.resolve(); }
  });
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {getUserMedia: () => new Promise(resolve => (grant = resolve))},
  });
  const browser = new BrowserAudio();
  const starting = browser.start(() => {}).catch(() => 'refused');
  browser.close();
  grant({getTracks: () => [{stop}]});
  expect(await starting).toBe('refused');
  expect(stop).toHaveBeenCalledTimes(1);
  vi.unstubAllGlobals();
});
