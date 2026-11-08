/**
 * Live voice chat client. Streams the microphone to the host as 24 kHz PCM16 and plays the reply the host relays
 * back. The host holds the provider connection and its key; nothing here is recorded or stored.
 *
 * Protocol (see src/termx/voice/live.py). Browser to host: binary PCM16 frames; JSON `mute`, `interrupt`, `end`.
 * Host to browser: binary PCM16; JSON `state`, `transcript`, `flush`, `error`, `ended`.
 */
export type LiveState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'ended';

export interface LiveHooks {
  onState: (state: LiveState) => void;
  /** 0 to 1. Microphone level while listening, reply level while the reply plays. */
  onLevel: (level: number) => void;
  onTranscript: (role: 'user' | 'assistant', text: string) => void;
  /** A sentence about something that went wrong but did not end the session. */
  onNotice: (message: string) => void;
  /** Called once, with a sentence that says what happened and, when it helps, what to do next. */
  onEnded: (reason: string, detail: string) => void;
}

/** Microphone capture and speaker playback. Replaced in tests; the browser version is `BrowserAudio`. */
export interface LiveAudio {
  start: (onFrame: (pcm: ArrayBuffer, level: number) => void) => Promise<void>;
  /** Queues PCM16 for playback and returns its level. */
  play: (pcm: ArrayBuffer) => number;
  flush: () => void;
  close: () => void;
}

export interface LiveDeps {
  socket: (url: string) => WebSocket;
  audio: () => LiveAudio;
}

const RATE = 24000;
const WORKLET_URL = '/assets/voice/live-worklet.js';
const CONFIRM_MS = 1500;
const CLOSE_DETAIL: Record<number, string> = {
  4401: 'Sign in again to use live voice.',
  4403: 'This account cannot use live voice in this conversation.',
  4404: 'This conversation is not available for live voice.',
  4409: 'Finish live voice setup in Voice settings, then start again.',
  4429: 'Another live voice session is already running. End it before starting a new one.',
};

export class BrowserAudio implements LiveAudio {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private next = 0;
  private closed = false;

  async start(onFrame: (pcm: ArrayBuffer, level: number) => void) {
    const Context =
      window.AudioContext ?? (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !Context || typeof AudioWorkletNode === 'undefined') {
      throw new Error('This browser cannot stream microphone audio.');
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
    if (this.closed) {
      // Ended while the permission prompt was open: release the microphone instead of capturing.
      stream.getTracks().forEach(track => track.stop());
      throw new Error('The session ended before the microphone started.');
    }
    this.stream = stream;
    try {
      const context = (this.context = new Context({latencyHint: 'interactive'}));
      await context.audioWorklet.addModule(WORKLET_URL);
      const node = (this.node = new AudioWorkletNode(context, 'pcm16-capture', {outputChannelCount: [1]}));
      node.port.onmessage = event => onFrame(event.data.pcm, event.data.level);
      const silent = context.createGain(); // keeps the capture node running without playing the microphone back
      silent.gain.value = 0;
      context.createMediaStreamSource(this.stream).connect(node);
      node.connect(silent).connect(context.destination);
      await context.resume();
    } catch (failure) {
      this.close();
      throw failure;
    }
  }

  play(pcm: ArrayBuffer) {
    const context = this.context;
    if (!context) return 0;
    const samples = new Int16Array(pcm.slice(0, pcm.byteLength - (pcm.byteLength % 2)));
    if (!samples.length) return 0;
    const floats = new Float32Array(samples.length);
    let energy = 0;
    for (let i = 0; i < samples.length; i++) {
      floats[i] = samples[i] / 32768;
      energy += floats[i] * floats[i];
    }
    const buffer = context.createBuffer(1, floats.length, RATE);
    buffer.copyToChannel(floats, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const at = Math.max(context.currentTime + 0.02, this.next);
    this.next = at + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
    source.start(at);
    return Math.min(1, Math.sqrt(energy / samples.length) * 3);
  }

  flush() {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already finished
      }
    }
    this.sources.clear();
    this.next = 0;
  }

  close() {
    this.closed = true;
    this.flush();
    this.stream?.getTracks().forEach(track => track.stop());
    this.node?.disconnect();
    void this.context?.close().catch(() => {});
    this.stream = this.node = this.context = null;
  }
}

const defaults: LiveDeps = {
  socket: url => new WebSocket(url),
  audio: () => new BrowserAudio(),
};

export class LiveVoiceClient {
  private socket: WebSocket | null = null;
  private audio: LiveAudio | null = null;
  private muted = false;
  private finished = false;
  private ending = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private state: LiveState = 'idle';

  constructor(
    private readonly hooks: LiveHooks,
    private readonly deps: LiveDeps = defaults,
  ) {}

  private set(state: LiveState) {
    if (state === this.state) return;
    this.state = state;
    this.hooks.onState(state);
  }

  private open() {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  private command(payload: Record<string, unknown>) {
    if (this.open()) this.socket!.send(JSON.stringify(payload));
  }

  async start(conversationId: string) {
    if (this.socket || this.audio || this.finished) return;
    this.set('connecting');
    const audio = (this.audio = this.deps.audio());
    try {
      await audio.start((pcm, level) => this.frame(pcm, level));
    } catch (failure) {
      if (this.finished || this.ending) return audio.close();
      const blocked = failure instanceof DOMException || (failure instanceof Error && /denied|permission/i.test(failure.message));
      return this.finish(
        'microphone',
        blocked
          ? 'The microphone is blocked. Allow it for this site in your browser, then start again.'
          : failure instanceof Error && failure.message
            ? failure.message
            : 'The microphone could not start.',
      );
    }
    if (this.finished || this.ending) {
      audio.close();
      return;
    }
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = (this.socket = this.deps.socket(
      `${protocol}//${location.host}/api/voice/live?conversation_id=${encodeURIComponent(conversationId)}`,
    ));
    socket.binaryType = 'arraybuffer';
    socket.onmessage = event => this.message(event.data);
    socket.onclose = event => {
      if (!this.finished) {
        this.finish('connection_lost', CLOSE_DETAIL[event.code] ?? 'The connection closed. Start again to keep talking.');
      }
    };
    document.addEventListener('visibilitychange', this.hidden);
    window.addEventListener('pagehide', this.leaving);
    window.addEventListener('termx-locked', this.locked);
  }

  private frame(pcm: ArrayBuffer, level: number) {
    if (this.finished || this.ending) return;
    if (this.muted) return this.hooks.onLevel(0);
    if (!this.open()) return;
    this.socket!.send(pcm);
    if (this.state !== 'speaking') this.hooks.onLevel(level);
  }

  private message(data: unknown) {
    if (this.finished) return;
    if (data instanceof ArrayBuffer) {
      if (!this.ending) this.hooks.onLevel(this.audio?.play(data) ?? 0);
      return;
    }
    let event: {type?: string; state?: LiveState; role?: string; text?: string; detail?: string; reason?: string};
    try {
      event = JSON.parse(String(data));
    } catch {
      return;
    }
    if (!event || typeof event !== 'object') return;
    if (event.type === 'state' && event.state) {
      if (this.ending) return;
      this.set(event.state);
      if (event.state !== 'speaking') this.hooks.onLevel(0);
    } else if (event.type === 'transcript' && (event.role === 'user' || event.role === 'assistant')) {
      this.hooks.onTranscript(event.role, String(event.text ?? ''));
    } else if (event.type === 'flush') {
      this.audio?.flush();
    } else if (event.type === 'error') {
      this.hooks.onNotice(String(event.detail ?? 'The voice provider reported an error.'));
    } else if (event.type === 'ended') {
      this.finish(String(event.reason ?? 'user'), String(event.detail ?? 'The session ended.'));
    }
  }

  mute(muted: boolean) {
    this.muted = muted;
    this.command({type: 'mute', muted});
    if (muted) this.hooks.onLevel(0);
  }

  /** The user talks over the reply, or presses Interrupt: silence the speaker now and tell the host to cancel. */
  interrupt() {
    this.audio?.flush();
    this.command({type: 'interrupt'});
  }

  /** Stops the microphone at once, asks the host to end, and finishes on its confirmation or after a short wait. */
  end(reason: 'user' | 'hidden' | 'locked' = 'user') {
    if (this.finished || this.ending) return;
    this.ending = true;
    this.command({type: 'end', reason});
    this.audio?.close();
    this.hooks.onLevel(0);
    this.timer = setTimeout(() => this.finish(reason, 'The session ended.'), CONFIRM_MS);
  }

  private hidden = () => {
    if (document.hidden) this.end('hidden');
  };
  private leaving = () => this.end('hidden');
  private locked = () => this.end('locked');

  private finish(reason: string, detail: string) {
    if (this.finished) return;
    this.finished = true;
    if (this.timer) clearTimeout(this.timer);
    document.removeEventListener('visibilitychange', this.hidden);
    window.removeEventListener('pagehide', this.leaving);
    window.removeEventListener('termx-locked', this.locked);
    this.audio?.close();
    const socket = this.socket;
    if (socket) {
      socket.onmessage = socket.onclose = null;
      try {
        socket.close(1000);
      } catch {
        // already closed
      }
    }
    this.hooks.onLevel(0);
    this.set('ended');
    this.hooks.onEnded(reason, detail);
  }
}
