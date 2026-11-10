/**
 * One owner of the microphone at a time. Dictation (composer-voice) and live voice (live-voice) are independent hooks, so
 * they agree here instead of opening two captures. Live voice is a deliberate, long session and always wins: starting it
 * stops a dictation in progress. Dictation never takes the microphone from a running live session and is refused.
 */
export type MicrophoneUser = 'dictation' | 'live';

export interface MicrophoneClaim {
  /** Gives the microphone back. Safe to call twice, and after another claim has replaced this one. */
  release: () => void;
}

interface Holder {
  user: MicrophoneUser;
  preempt: () => void;
}

let holder: Holder | null = null;

/**
 * Returns a claim, or null when the microphone is busy with something that must not be interrupted.
 * `preempt` runs when a higher-priority user takes the microphone; it should stop capturing at once.
 */
export function claimMicrophone(user: MicrophoneUser, preempt: () => void): MicrophoneClaim | null {
  if (holder && holder.user === 'live' && user !== 'live') return null;
  const previous = holder;
  const mine: Holder = {user, preempt};
  holder = mine;
  if (previous && previous.user !== user) previous.preempt();
  return {
    release: () => {
      if (holder === mine) holder = null;
    },
  };
}

export function microphoneHeldBy(): MicrophoneUser | null {
  return holder?.user ?? null;
}
