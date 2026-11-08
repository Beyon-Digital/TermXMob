/**
 * Microphone recording shared by the composer, the voice panel and the media page.
 *
 * One recording at a time. A permission prompt that is answered after the recording was cancelled, hidden or
 * locked releases its tracks and never starts a recorder.
 */
export type RecorderPhase = 'idle' | 'requesting' | 'recording';

export const MAX_RECORDING_BYTES = 10 * 1024 * 1024;
export const MAX_RECORDING_MS = 180_000;
const MIME_TYPES = ['audio/webm', 'audio/mp4', 'audio/ogg'];
const UNAVAILABLE = 'Microphone recording is unavailable here. Upload an audio file instead.';

export interface RecorderOptions {
  maxBytes?: number;
  maxMs?: number;
  /** Asked again after the browser grants the microphone; false releases it unused. */
  allowed?: () => boolean;
  onPhase?: (phase: RecorderPhase) => void;
  /** 0 to 1, about fifteen times a second while recording, then 0. */
  onLevel?: (level: number) => void;
  /** Called once per finished recording; never for a cancelled or oversized one. */
  onRecording: (blob: Blob) => void;
  onError: (message: string) => void;
}

interface Session {
  media: MediaStream;
  instance: MediaRecorder;
  timer: ReturnType<typeof setTimeout> | null;
  stopMeter: () => void;
}

function releaseTracks(media: MediaStream) {
  media.getTracks().forEach(track => track.stop());
}

function startMeter(media: MediaStream, onLevel?: (level: number) => void): () => void {
  const Context: typeof AudioContext | undefined =
    typeof window === 'undefined'
      ? undefined
      : (window.AudioContext ?? (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext);
  if (!onLevel || !Context || typeof requestAnimationFrame !== 'function') return () => {};
  try {
    const context = new Context();
    const source = context.createMediaStreamSource(media);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    let frame = 0;
    let last = 0;
    const tick = (now: number) => {
      if (now - last >= 66) {
        last = now;
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const value of samples) sum += ((value - 128) / 128) ** 2;
        onLevel(Math.min(1, Math.sqrt(sum / samples.length) * 3));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      source.disconnect();
      void context.close().catch(() => {});
      onLevel(0);
    };
  } catch {
    return () => {};
  }
}

function describe(error: unknown) {
  const name = error instanceof Error || error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Microphone access was blocked. Allow it in your browser settings, or upload an audio file.';
  }
  if (name === 'NotFoundError') return 'No microphone was found. Connect one, or upload an audio file.';
  return 'The microphone could not start. Allow access, or upload an audio file.';
}

export class VoiceRecorder {
  phase: RecorderPhase = 'idle';
  private ticket = 0;
  private session: Session | null = null;

  constructor(private readonly options: RecorderOptions) {}

  private setPhase(phase: RecorderPhase) {
    this.phase = phase;
    this.options.onPhase?.(phase);
  }

  async start(): Promise<void> {
    if (this.phase !== 'idle') return;
    const ticket = ++this.ticket;
    const maxBytes = this.options.maxBytes ?? MAX_RECORDING_BYTES;
    this.setPhase('requesting');
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        throw new Error(UNAVAILABLE);
      }
      const media = await navigator.mediaDevices.getUserMedia({audio: true});
      if (ticket !== this.ticket || this.options.allowed?.() === false) {
        releaseTracks(media);
        if (ticket === this.ticket) this.setPhase('idle');
        return;
      }
      const mime = MIME_TYPES.find(type => MediaRecorder.isTypeSupported?.(type));
      const instance = new MediaRecorder(media, mime ? {mimeType: mime} : undefined);
      const chunks: Blob[] = [];
      let bytes = 0;
      let overflowed = false;
      const session: Session = {media, instance, timer: null, stopMeter: startMeter(media, this.options.onLevel)};
      this.session = session;
      instance.ondataavailable = event => {
        if (ticket !== this.ticket) return;
        bytes += event.data.size;
        chunks.push(event.data);
        if (bytes > maxBytes && !overflowed) {
          overflowed = true;
          this.options.onError('Recording exceeded 10 MB; make a shorter recording.');
          this.stop();
        }
      };
      instance.onerror = () => {
        if (ticket !== this.ticket) return;
        this.cancel();
        this.options.onError('Recording failed. Upload an audio file, or allow the microphone and try again.');
      };
      instance.onstop = () => {
        releaseTracks(media);
        session.stopMeter();
        if (session.timer) clearTimeout(session.timer);
        if (ticket !== this.ticket) return;
        this.session = null;
        this.setPhase('idle');
        if (!overflowed) this.options.onRecording(new Blob(chunks, {type: instance.mimeType || mime || 'audio/webm'}));
      };
      instance.start(1000);
      session.timer = setTimeout(() => this.stop(), this.options.maxMs ?? MAX_RECORDING_MS);
      this.setPhase('recording');
    } catch (error) {
      if (ticket !== this.ticket) return;
      this.setPhase('idle');
      this.options.onError(error instanceof Error && error.message === UNAVAILABLE ? UNAVAILABLE : describe(error));
    }
  }

  /** Finish and deliver what was recorded. */
  stop() {
    const instance = this.session?.instance;
    if (instance && instance.state !== 'inactive') instance.stop();
  }

  /** Throw the recording away, release the microphone and ignore any answer still pending. */
  cancel() {
    const wasIdle = this.phase === 'idle';
    this.ticket++;
    const session = this.session;
    this.session = null;
    if (session) {
      if (session.timer) clearTimeout(session.timer);
      session.stopMeter();
      releaseTracks(session.media);
      if (session.instance.state !== 'inactive') session.instance.stop();
    }
    if (!wasIdle) this.setPhase('idle');
  }
}
