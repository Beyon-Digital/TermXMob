import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {SpeechPlayer} from './player';
import type {SpeechEvent} from './speech-stream';
import {speechEvents} from './speech-stream';

vi.mock('./speech-stream', () => ({speechEvents: vi.fn()}));
const events = vi.mocked(speechEvents);

const pcm = (values: number[]) => btoa(String.fromCharCode(...new Uint8Array(new Float32Array(values).buffer)));

function script(...items: (SpeechEvent | Error)[]) {
  events.mockImplementation(async function* () {
    for (const item of items) {
      if (item instanceof Error) throw item;
      yield item;
    }
  });
}

class FakeContext {
  static made: FakeContext[] = [];
  currentTime = 0;
  state = 'running';
  destination = {};
  started: {duration: number; at: number; samples: number[]}[] = [];
  closed = false;
  constructor() {
    FakeContext.made.push(this);
  }
  createBuffer(_channels: number, length: number, rate: number) {
    let data: Float32Array = new Float32Array(length);
    return {duration: length / rate, copyToChannel: (samples: Float32Array) => (data = samples), data: () => data};
  }
  createBufferSource() {
    const context = this;
    return {
      buffer: null as null | ReturnType<FakeContext['createBuffer']>,
      onended: null as null | (() => void),
      connect() {},
      stop() {},
      start(at: number) {
        context.started.push({at, duration: this.buffer!.duration, samples: Array.from(this.buffer!.data())});
        queueMicrotask(() => this.onended?.());
      },
    };
  }
  resume() {
    return Promise.resolve();
  }
  close() {
    this.closed = true;
    return Promise.resolve();
  }
}

function hooks() {
  const seen = {states: [] as string[], credits: [] as unknown[], notices: [] as string[]};
  return {
    seen,
    hooks: {
      onState: (state: string) => seen.states.push(state),
      onCredit: (credit: unknown) => seen.credits.push(credit),
      onNotice: (notice: string) => seen.notices.push(notice),
    },
  };
}

let spoken: string[];
let cancelled: ReturnType<typeof vi.fn>;
beforeEach(() => {
  FakeContext.made = [];
  spoken = [];
  cancelled = vi.fn();
  vi.stubGlobal('AudioContext', FakeContext);
  vi.stubGlobal(
    'SpeechSynthesisUtterance',
    class {
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public text: string) {}
    },
  );
  vi.stubGlobal('speechSynthesis', {
    speak: (utterance: {text: string; onend: () => void}) => {
      spoken.push(utterance.text);
      queueMicrotask(() => utterance.onend());
    },
    getVoices: () => [{name: 'Cloud', localService: false}, {name: 'Local', localService: true}],
    cancel: cancelled,
  });
  events.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

it('plays chunks in order, shows the credit while Woof speaks and clears it afterwards', async () => {
  const credit = {text: 'Voice by Underdog Woof', url: 'https://underdog.ai'};
  script(
    {event: 'start', engine: 'woof', voice: 'juno', sample_rate: 24000, attribution: credit},
    {event: 'chunk', index: 0, text: 'One.', pcm: pcm([0.5, -0.5])},
    {event: 'chunk', index: 1, text: 'Two.', pcm: pcm([0.25])},
    {event: 'end'},
  );
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('One. Two.');
  const context = FakeContext.made[0];
  expect(context.started.map(item => item.samples)).toEqual([[0.5, -0.5], [0.25]]);
  expect(context.started[1].at).toBeGreaterThanOrEqual(context.started[0].at + context.started[0].duration - 1e-9);
  expect(seen.credits).toEqual([credit, null]);
  expect(seen.states).toEqual(['loading', 'speaking', 'idle']);
  expect(spoken).toEqual([]);
});

it('speaks no credit for an engine that needs none', async () => {
  script({event: 'start', engine: 'provider', voice: 'alloy', sample_rate: 24000}, {event: 'end'});
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('Hello there.');
  expect(seen.credits).toEqual([]);
});

it('falls back to the browser voice and says so when the host cannot speak', async () => {
  script(new Error('Woof is not ready; download it in Voice settings'));
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('Hello there. Second sentence.');
  expect(seen.notices[0]).toMatch(/browser voice/i);
  expect(seen.notices[0]).toMatch(/Woof is not ready/);
  expect(seen.notices[0]).toContain('Voice settings. Reading with your browser voice instead.');
  expect(spoken).toEqual(['Hello there.', 'Second sentence.']);
  expect(seen.states.at(-1)).toBe('idle');
});

it('does not switch voices in the middle of a reply', async () => {
  script(
    {event: 'start', engine: 'provider', voice: 'alloy', sample_rate: 24000},
    {event: 'chunk', index: 0, text: 'One.', pcm: pcm([0.1])},
    {event: 'error', detail: 'Provider unavailable'},
  );
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('One. Two.');
  expect(spoken).toEqual([]);
  expect(seen.notices[0]).toMatch(/stopped/i);
});

it('uses the browser voice directly when that is the chosen engine', async () => {
  const {hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('Just the browser.', {engine: 'browser'});
  expect(events).not.toHaveBeenCalled();
  expect(spoken).toEqual(['Just the browser.']);
});

it('stop cancels the stream, the queue and the browser voice', async () => {
  let release!: () => void;
  const signals: AbortSignal[] = [];
  events.mockImplementation(async function* (_body, signal) {
    signals.push(signal);
    yield {event: 'start', engine: 'provider', voice: 'alloy', sample_rate: 24000} as SpeechEvent;
    await new Promise<void>(done => (release = done));
  });
  const {seen, hooks: callbacks} = hooks();
  const player = new SpeechPlayer(callbacks);
  const playing = player.speak('Long reply.');
  await vi.waitFor(() => expect(seen.states).toContain('speaking'));
  player.stop();
  release();
  await playing;
  expect(signals[0].aborted).toBe(true);
  expect(FakeContext.made[0].closed).toBe(true);
  expect(cancelled).toHaveBeenCalled();
  expect(seen.states.at(-1)).toBe('idle');
});

it('says what to do when nothing can speak', async () => {
  vi.stubGlobal('speechSynthesis', undefined);
  script(new Error('Choose an API account first'));
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('Hello.');
  expect(seen.notices.join(' ')).toMatch(/cannot read aloud/i);
});

it('ignores text with nothing to say', async () => {
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('   ');
  expect(events).not.toHaveBeenCalled();
  expect(seen.states).toEqual([]);
});

it('reads a fallback with an on-device browser voice only', async () => {
  const used: unknown[] = [];
  vi.stubGlobal('speechSynthesis', {
    speak: (utterance: {text: string; voice: unknown; onend: () => void}) => {
      used.push(utterance.voice);
      queueMicrotask(() => utterance.onend());
    },
    getVoices: () => [{name: 'Cloud', localService: false}, {name: 'Local', localService: true}],
    cancel: cancelled,
  });
  script(new Error('Woof is not ready'));
  await new SpeechPlayer(hooks().hooks).speak('Hello there.');
  expect(used).toEqual([{name: 'Local', localService: true}]);
});

it('stays silent instead of sending text to a network browser voice', async () => {
  const speak = vi.fn();
  vi.stubGlobal('speechSynthesis', {
    speak,
    getVoices: () => [{name: 'Cloud', localService: false}],
    cancel: cancelled,
  });
  script({event: 'error', detail: 'Woof is not ready'});
  const {seen, hooks: callbacks} = hooks();
  await new SpeechPlayer(callbacks).speak('Private reply.');
  expect(speak).not.toHaveBeenCalled();
  expect(seen.notices[0]).toMatch(/Woof is not ready/);
  expect(seen.notices[0]).toMatch(/on-device/i);
  expect(seen.states.at(-1)).toBe('idle');
});

it('plays at the sample rate the host announces', async () => {
  script(
    {event: 'start', engine: 'provider', voice: 'alloy', sample_rate: 16000},
    {event: 'chunk', index: 0, text: 'One.', pcm: pcm([0.5, 0.5, 0.5, 0.5])},
    {event: 'end'},
  );
  await new SpeechPlayer(hooks().hooks).speak('One.');
  expect(FakeContext.made[0].started[0].duration).toBeCloseTo(4 / 16000);
});
