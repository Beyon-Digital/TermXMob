import {act, cleanup, render, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {request} from '../lib/api';
import {transfer} from '../lib/transfers';
import type {CharacterManifest} from '../lib/characters';
import CharacterSprite, {useCharacterManifest} from './CharacterSprite';

vi.mock('../lib/api', () => ({request: vi.fn()}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn()}));

const frame = (duration_ms: number) => ({atlas: 0, x: 0, y: 0, width: 96, height: 112, duration_ms});
const manifest: CharacterManifest = {
  version: 1,
  id: 'pack',
  name: 'Sprout',
  revision: 1,
  logical_size: {width: 96, height: 112},
  foot_anchor: {x: 48, y: 104},
  interaction_bounds: {x: 0, y: 0, width: 96, height: 112},
  atlases: [{file: 'atlas.png', width: 96, height: 336}],
  states: {idle: 'idle', waiting: 'waiting'},
  clips: {
    idle: {loop: true, frames: [frame(100), frame(100), frame(100)]},
    waiting: {loop: true, frames: [frame(100)]},
  },
};
const context = {clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(), scale: vi.fn(), translate: vi.fn(), drawImage: vi.fn()};
const created: string[] = [];
const revoked: string[] = [];
let ids = 0;

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn()}));
  vi.stubGlobal('Image', class {
    onload: (() => void) | null = null;
    set src(_: string) {
      queueMicrotask(() => this.onload?.());
    }
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  // jsdom has no object URLs, so install recorders for the test and let the file's afterEach drop them.
  Object.assign(URL, {
    createObjectURL: () => {
      const url = 'blob:atlas-' + ids++;
      created.push(url);
      return url;
    },
    revokeObjectURL: (url: string) => void revoked.push(url),
  });
  vi.mocked(request).mockResolvedValue(structuredClone(manifest));
  vi.mocked(transfer).mockResolvedValue(new Blob(['png']));
  Object.values(context).forEach(mock => mock.mockClear());
  created.length = 0;
  revoked.length = 0;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(request).mockReset();
  vi.mocked(transfer).mockReset();
  Object.defineProperty(document, 'hidden', {configurable: true, value: false});
});

const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(0)));
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

it('waits for a real selected revision before fetching its manifest', async () => {
  vi.mocked(request).mockRejectedValue(new Error('Fixture unavailable'));
  const view = renderHook(({revision}) => useCharacterManifest('termx-robot', revision), {initialProps: {revision: 0}});
  expect(request).not.toHaveBeenCalled();
  view.rerender({revision: 1});
  await waitFor(() => expect(view.result.current.error).toBe('Fixture unavailable'));
  expect(request).toHaveBeenCalledExactlyOnceWith('/api/characters/packs/termx-robot/manifest?revision=1');
});

it('draws only when the frame changes, on the time each frame is due', async () => {
  vi.useFakeTimers();
  render(<CharacterSprite packId="draws" revision={1} />);
  await settle();
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  await advance(99);
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  await advance(1);
  expect(context.drawImage).toHaveBeenCalledTimes(2);
  await advance(1000);
  expect(context.drawImage).toHaveBeenCalledTimes(12);
});

it('draws a single-frame animation once and then stays idle', async () => {
  vi.useFakeTimers();
  render(<CharacterSprite packId="single" revision={1} state="waiting" />);
  await settle();
  await advance(5000);
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it('draws once and schedules nothing when paused, reduced or showing a chosen frame', async () => {
  vi.useFakeTimers();
  const paused = render(<CharacterSprite packId="paused" revision={1} paused />);
  await settle();
  await advance(2000);
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  paused.unmount();
  context.drawImage.mockClear();
  render(<CharacterSprite packId="still" revision={1} reducedMotion />);
  await settle();
  await advance(2000);
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it('redraws on a scrubbed frame change without resizing the canvas', async () => {
  vi.useFakeTimers();
  const view = render(<CharacterSprite packId="scrub" revision={1} frame={0} />);
  await settle();
  const canvas = view.container.querySelector('canvas');
  const widthSetter = vi.fn();
  Object.defineProperty(canvas, 'width', {configurable: true, get: () => 96, set: widthSetter});
  expect(context.drawImage).toHaveBeenCalledTimes(1);
  view.rerender(<CharacterSprite packId="scrub" revision={1} frame={2} />);
  expect(context.drawImage).toHaveBeenCalledTimes(2);
  expect(context.drawImage.mock.calls[1][1]).toBe(0);
  expect(widthSetter).not.toHaveBeenCalled();
});

it('stops the timer while the tab is hidden and resumes when it returns', async () => {
  vi.useFakeTimers();
  render(<CharacterSprite packId="hidden" revision={1} />);
  await settle();
  Object.defineProperty(document, 'hidden', {configurable: true, value: true});
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  const before = context.drawImage.mock.calls.length;
  await advance(2000);
  expect(context.drawImage).toHaveBeenCalledTimes(before);
  Object.defineProperty(document, 'hidden', {configurable: true, value: false});
  act(() => void document.dispatchEvent(new Event('visibilitychange')));
  await advance(150);
  expect(context.drawImage.mock.calls.length).toBeGreaterThan(before);
});

it('revokes every artwork URL once the last sprite using a revision unmounts', async () => {
  const first = render(<CharacterSprite packId="shared" revision={1} />);
  const second = render(<CharacterSprite packId="shared" revision={1} />);
  await waitFor(() => expect(context.drawImage).toHaveBeenCalled());
  expect(created).toHaveLength(1);
  first.unmount();
  await Promise.resolve();
  expect(revoked).toHaveLength(0);
  second.unmount();
  await waitFor(() => expect(revoked).toEqual(created));
});

it('does not fetch artwork for a lazy sprite until it nears the viewport', async () => {
  let notify: ((entries: {isIntersecting: boolean}[]) => void) | undefined;
  const disconnect = vi.fn();
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: (entries: {isIntersecting: boolean}[]) => void) {
      notify = callback;
    }
    observe() {}
    disconnect = disconnect;
  });
  render(<CharacterSprite packId="lazy" revision={1} lazy />);
  await Promise.resolve();
  expect(request).not.toHaveBeenCalled();
  act(() => notify?.([{isIntersecting: true}]));
  await waitFor(() => expect(request).toHaveBeenCalledExactlyOnceWith('/api/characters/packs/lazy/manifest?revision=1'));
  expect(disconnect).toHaveBeenCalled();
});

it('reports the manifest once and shows a readable error when artwork fails', async () => {
  const onManifest = vi.fn();
  const ok = render(<CharacterSprite packId="notify" revision={1} onManifest={onManifest} />);
  await waitFor(() => expect(onManifest).toHaveBeenCalledTimes(1));
  expect(onManifest.mock.calls[0][0].name).toBe('Sprout');
  ok.unmount();
  vi.mocked(request).mockRejectedValue(new Error('Gone'));
  const failed = render(<CharacterSprite packId="broken" revision={1} />);
  expect(await failed.findByRole('img', {name: 'Gone'})).toBeInTheDocument();
});
