import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {VoiceRecorder} from './recorder';

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported() {
    return true;
  }
  state = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((event: {data: Blob}) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    FakeRecorder.instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({data: new Blob(['voice'])});
    this.onstop?.();
  }
}

function stream() {
  const stop = vi.fn();
  return {media: {getTracks: () => [{stop}]} as unknown as MediaStream, stop};
}

function build(overrides: Record<string, unknown> = {}) {
  const calls = {phases: [] as string[], recordings: [] as Blob[], errors: [] as string[], levels: [] as number[]};
  const recorder = new VoiceRecorder({
    onPhase: phase => calls.phases.push(phase),
    onRecording: blob => calls.recordings.push(blob),
    onError: message => calls.errors.push(message),
    onLevel: level => calls.levels.push(level),
    ...overrides,
  });
  return {recorder, calls};
}

beforeEach(() => {
  FakeRecorder.instances = [];
  vi.stubGlobal('MediaRecorder', FakeRecorder);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('records, stops and hands back one blob with the recorder mime type', async () => {
  const {media, stop} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const {recorder, calls} = build();
  await recorder.start();
  expect(recorder.phase).toBe('recording');
  recorder.stop();
  expect(calls.recordings).toHaveLength(1);
  expect(calls.recordings[0].type).toBe('audio/webm');
  expect(stop).toHaveBeenCalled();
  expect(calls.phases).toEqual(['requesting', 'recording', 'idle']);
});

it('asks for the microphone once while a request is pending', async () => {
  let resolve!: (value: unknown) => void;
  const getUserMedia = vi.fn(() => new Promise(done => (resolve = done)));
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia}});
  const {recorder} = build();
  const first = recorder.start();
  void recorder.start();
  expect(getUserMedia).toHaveBeenCalledOnce();
  const {media} = stream();
  resolve(media);
  await first;
});

it('releases a permission granted after cancel and never builds a recorder', async () => {
  let resolve!: (value: unknown) => void;
  const {media, stop} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: () => new Promise(done => (resolve = done))}});
  const {recorder, calls} = build();
  const pending = recorder.start();
  recorder.cancel();
  resolve(media);
  await pending;
  expect(stop).toHaveBeenCalledOnce();
  expect(FakeRecorder.instances).toHaveLength(0);
  expect(recorder.phase).toBe('idle');
  expect(calls.recordings).toHaveLength(0);
});

it('releases a permission granted while recording is not allowed', async () => {
  const {media, stop} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const {recorder} = build({allowed: () => false});
  await recorder.start();
  expect(stop).toHaveBeenCalledOnce();
  expect(FakeRecorder.instances).toHaveLength(0);
  expect(recorder.phase).toBe('idle');
});

it('discards a cancelled recording and stops the microphone', async () => {
  const {media, stop} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const {recorder, calls} = build();
  await recorder.start();
  recorder.cancel();
  expect(calls.recordings).toHaveLength(0);
  expect(stop).toHaveBeenCalled();
  expect(recorder.phase).toBe('idle');
});

it('ignores late events from a cancelled recorder after a new recording starts', async () => {
  const first = stream();
  const second = stream();
  const getUserMedia = vi.fn().mockResolvedValueOnce(first.media).mockResolvedValueOnce(second.media);
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia}});
  const {recorder, calls} = build();
  await recorder.start();
  recorder.cancel();
  await recorder.start();
  FakeRecorder.instances[0].ondataavailable?.({data: new Blob(['old'])});
  FakeRecorder.instances[0].onstop?.();
  expect(recorder.phase).toBe('recording');
  expect(calls.recordings).toHaveLength(0);
  expect(second.stop).not.toHaveBeenCalled();
});

it('stops at the size limit, reports it and delivers nothing', async () => {
  const {media} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const {recorder, calls} = build({maxBytes: 3});
  await recorder.start();
  FakeRecorder.instances[0].ondataavailable?.({data: new Blob(['too long'])});
  expect(calls.errors[0]).toMatch(/exceeded/i);
  expect(calls.recordings).toHaveLength(0);
  expect(recorder.phase).toBe('idle');
});

it('stops by itself at the time limit and still delivers the recording', async () => {
  vi.useFakeTimers();
  const {media} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const {recorder, calls} = build({maxMs: 1000});
  await recorder.start();
  vi.advanceTimersByTime(1001);
  expect(calls.recordings).toHaveLength(1);
});

it('says what to do when recording is unavailable or blocked', async () => {
  vi.stubGlobal('navigator', {});
  const unavailable = build();
  await unavailable.recorder.start();
  expect(unavailable.calls.errors[0]).toMatch(/unavailable/i);
  expect(unavailable.calls.errors[0]).not.toMatch(/upload/i);
  expect(unavailable.recorder.phase).toBe('idle');
  const denied = Object.assign(new Error('x'), {name: 'NotAllowedError'});
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockRejectedValue(denied)}});
  const blocked = build();
  await blocked.recorder.start();
  expect(blocked.calls.errors[0]).toMatch(/blocked/i);
  expect(blocked.recorder.phase).toBe('idle');
});

it('reports a level while recording and closes the audio graph afterwards', async () => {
  const {media} = stream();
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: vi.fn().mockResolvedValue(media)}});
  const close = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal(
    'AudioContext',
    class {
      createMediaStreamSource() {
        return {connect() {}, disconnect() {}};
      }
      createAnalyser() {
        return {
          fftSize: 0,
          getByteTimeDomainData(data: Uint8Array) {
            data.fill(192);
          },
        };
      }
      close = close;
    },
  );
  const frames: ((time: number) => void)[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: (time: number) => void) => (frames.push(callback), 1));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  const {recorder, calls} = build();
  await recorder.start();
  frames[0](100);
  expect(calls.levels.at(-1)).toBeGreaterThan(0.2);
  recorder.stop();
  expect(close).toHaveBeenCalled();
  expect(calls.levels.at(-1)).toBe(0);
});
