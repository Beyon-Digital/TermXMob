import {ApiError, csrf, locked, refreshSession} from '../api';
import {nativeWorkspace} from '../native';
import {transfer} from '../transfers';
import type {Attribution} from './settings';

export type SpeechEvent =
  | {event: 'start'; engine: string; voice: string; sample_rate: number; attribution?: Attribution}
  | {event: 'chunk'; index: number; text: string; pcm: string}
  | {event: 'end'}
  | {event: 'error'; detail: string};

export interface SpeechRequest {
  text: string;
  voice?: string;
  engine?: string;
}

function parse(line: string): SpeechEvent | null {
  if (!line.trim()) return null;
  const value = JSON.parse(line) as SpeechEvent;
  return typeof value?.event === 'string' ? value : null;
}

/** The host's per-sentence audio stream, one event at a time as the lines arrive. */
export async function* speechEvents(body: SpeechRequest, signal: AbortSignal, retry = true): AsyncGenerator<SpeechEvent> {
  const init = {
    method: 'POST',
    body: JSON.stringify(body),
    headers: {'Content-Type': 'application/json'},
    signal,
  };
  if (nativeWorkspace()) {
    // The native bridge returns the whole body at once, so playback starts when the last sentence is ready.
    for (const line of (await (await transfer('/api/voice/speak', init)).text()).split('\n')) {
      const event = parse(line);
      if (event) yield event;
    }
    return;
  }
  const response = await fetch('/api/voice/speak', {
    ...init,
    headers: {...init.headers, 'X-Termx-CSRF': csrf()},
    credentials: 'same-origin',
  });
  if (response.status === 401 && retry) {
    await refreshSession();
    yield* speechEvents(body, signal, false);
    return;
  }
  if (response.status === 423) locked();
  if (!response.ok || !response.body) {
    const failure = await response.json().catch(() => ({detail: response.statusText}));
    throw new ApiError(response.status, typeof failure.detail === 'string' ? failure.detail : response.statusText);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    for (;;) {
      const {done, value} = await reader.read();
      pending += decoder.decode(value, {stream: !done});
      const lines = pending.split('\n');
      pending = lines.pop() ?? '';
      for (const line of lines) {
        const event = parse(line);
        if (event) yield event;
      }
      if (done) break;
    }
    const last = parse(pending);
    if (last) yield last;
  } finally {
    void reader.cancel().catch(() => {});
  }
}
