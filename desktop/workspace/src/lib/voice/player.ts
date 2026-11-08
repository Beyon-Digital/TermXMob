import {speechEvents} from './speech-stream';
import {speechText} from './speech-text';
import type {Attribution} from './settings';

export type PlayerState = 'idle' | 'loading' | 'speaking';

export interface PlayerHooks {
  onState: (state: PlayerState) => void;
  /** Set while audio that needs a credit is playing, null afterwards. */
  onCredit: (credit: Attribution | null) => void;
  /** A sentence for the status line: a fallback happened or reading stopped. */
  onNotice: (notice: string) => void;
}

export interface SpeakOptions {
  /** 'browser' uses the browser voice. Leave unset to follow the saved engine and its cloud fallback. */
  engine?: 'browser' | 'woof' | 'provider';
  voice?: string;
  /** Set false for a preview, which should say why the engine failed instead of switching to the browser voice. */
  fallback?: boolean;
}

function decode(pcm: string): Float32Array {
  const text = atob(pcm);
  const bytes = new Uint8Array(text.length - (text.length % 4));
  for (let i = 0; i < bytes.length; i++) bytes[i] = text.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

/** Schedules decoded chunks back to back so the next sentence starts as the last one ends. */
class PcmQueue {
  private readonly context: AudioContext;
  private next = 0;
  private pending = 0;
  private waiting: (() => void)[] = [];

  constructor() {
    const Context =
      window.AudioContext ?? (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
    if (!Context) throw new Error('This browser cannot play streamed audio.');
    this.context = new Context();
    void this.context.resume?.();
  }

  push(samples: Float32Array, rate: number) {
    const buffer = this.context.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    const at = Math.max(this.context.currentTime + 0.02, this.next);
    this.next = at + buffer.duration;
    this.pending++;
    source.onended = () => {
      if (--this.pending === 0) this.waiting.splice(0).forEach(resolve => resolve());
    };
    source.start(at);
  }

  drain() {
    return this.pending === 0 ? Promise.resolve() : new Promise<void>(resolve => this.waiting.push(resolve));
  }

  close() {
    this.waiting.splice(0).forEach(resolve => resolve());
    void this.context.close().catch(() => {});
  }
}

/**
 * Reads text aloud. The host speaks first (the chosen engine, then the cloud account if the owner allowed that
 * fallback); if it cannot start, the browser voice reads instead. Once audio has started it never switches.
 */
export class SpeechPlayer {
  private ticket = 0;
  private controller: AbortController | null = null;
  private queue: PcmQueue | null = null;
  private browserDone: (() => void) | null = null;
  private state: PlayerState = 'idle';
  private credited = false;
  private rate = 24000;

  constructor(private readonly hooks: PlayerHooks) {}

  private setState(state: PlayerState) {
    if (state === this.state) return;
    this.state = state;
    this.hooks.onState(state);
  }

  private setCredit(credit: Attribution | null) {
    if (!credit && !this.credited) return;
    this.credited = !!credit;
    this.hooks.onCredit(credit);
  }

  async speak(markdown: string, options: SpeakOptions = {}): Promise<void> {
    const text = speechText(markdown);
    if (!text) return;
    this.stop();
    const ticket = this.ticket;
    const current = () => ticket === this.ticket;
    this.setState('loading');
    let started = false;
    try {
      if (options.engine === 'browser') {
        await this.browser(text, current);
        return;
      }
      const controller = (this.controller = new AbortController());
      try {
        for await (const event of speechEvents({text, voice: options.voice, engine: options.engine}, controller.signal)) {
          if (!current()) return;
          if (event.event === 'start') {
            started = true;
            this.rate = event.sample_rate > 0 ? event.sample_rate : 24000;
            this.queue = new PcmQueue();
            if (event.attribution) this.setCredit(event.attribution);
            this.setState('speaking');
          } else if (event.event === 'chunk') {
            this.queue?.push(decode(event.pcm), this.rate);
          } else if (event.event === 'end') {
            await this.queue?.drain();
          } else if (event.event === 'error') {
            throw new Error(event.detail);
          }
        }
      } catch (error) {
        if (!current() || controller.signal.aborted) return;
        const reason = error instanceof Error ? error.message : 'The voice engine failed.';
        if (started) {
          this.hooks.onNotice(`Reading stopped: ${reason}`);
        } else if (options.fallback === false) {
          this.hooks.onNotice(reason);
        } else {
          await this.browser(text, current, reason);
        }
      }
    } finally {
      if (current()) {
        this.release();
        this.setCredit(null);
        this.setState('idle');
      }
    }
  }

  stop() {
    this.ticket++;
    this.controller?.abort();
    this.release();
    this.setCredit(null);
    this.setState('idle');
    if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
    this.browserDone?.();
  }

  private release() {
    this.queue?.close();
    this.queue = null;
    this.controller = null;
    this.browserDone = null;
  }

  /**
   * `failure` is set when the host engine could not start. Some browser voices stream text to a vendor, so that
   * fallback reads only with a voice that runs on this device, and says so when there is none.
   */
  private browser(text: string, current: () => boolean, failure?: string): Promise<void> {
    if (typeof speechSynthesis === 'undefined' || typeof SpeechSynthesisUtterance === 'undefined') {
      this.hooks.onNotice('This browser cannot read aloud. Turn on a voice engine in Settings, Voice.');
      return Promise.resolve();
    }
    let voice: SpeechSynthesisVoice | undefined;
    if (failure !== undefined) {
      voice = speechSynthesis.getVoices?.().find(item => item.localService);
      if (!voice) {
        this.hooks.onNotice(`${failure} No on-device browser voice is installed, so nothing was read aloud.`);
        return Promise.resolve();
      }
      this.hooks.onNotice(`${failure} Reading with your browser voice instead.`);
    }
    this.setState('speaking');
    const sentences = text.split(/(?<=[.!?])\s+/).filter(Boolean);
    return new Promise<void>(resolve => {
      this.browserDone = resolve;
      sentences.forEach((sentence, index) => {
        const utterance = new SpeechSynthesisUtterance(sentence);
        if (voice) utterance.voice = voice;
        const finished = () => {
          if (index === sentences.length - 1 || !current()) resolve();
        };
        utterance.onend = finished;
        utterance.onerror = finished;
        speechSynthesis.speak(utterance);
      });
    });
  }
}
