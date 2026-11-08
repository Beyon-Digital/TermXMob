import {expect, it, vi} from 'vitest';
import {claimMicrophone, microphoneHeldBy} from './microphone';

it('lets one user hold the microphone and frees it on release', () => {
  const dictation = claimMicrophone('dictation', vi.fn());
  expect(dictation).not.toBeNull();
  expect(microphoneHeldBy()).toBe('dictation');
  dictation!.release();
  dictation!.release();
  expect(microphoneHeldBy()).toBeNull();
});

it('stops dictation when live voice starts', () => {
  const stopDictation = vi.fn();
  const dictation = claimMicrophone('dictation', stopDictation)!;
  const live = claimMicrophone('live', vi.fn());
  expect(live).not.toBeNull();
  expect(stopDictation).toHaveBeenCalledTimes(1);
  expect(microphoneHeldBy()).toBe('live');
  // The stopped dictation's late release must not free the microphone that live voice now holds.
  dictation.release();
  expect(microphoneHeldBy()).toBe('live');
  live!.release();
  expect(microphoneHeldBy()).toBeNull();
});

it('refuses dictation while live voice holds the microphone and never preempts live voice', () => {
  const stopLive = vi.fn();
  const live = claimMicrophone('live', stopLive)!;
  expect(claimMicrophone('dictation', vi.fn())).toBeNull();
  expect(stopLive).not.toHaveBeenCalled();
  expect(microphoneHeldBy()).toBe('live');
  live.release();
  expect(claimMicrophone('dictation', vi.fn())).not.toBeNull();
});
