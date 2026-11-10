import {strokeMatches} from '../keybindings';

/** Releasing sooner than this counts as a tap, which keeps recording until the next press. */
export const TAP_MS = 350;

export interface PushToTalkApi {
  binding: () => string;
  isRecording: () => boolean;
  start: () => void;
  stop: () => void;
  now?: () => number;
}

/** Hold the shortcut to talk. A quick tap starts a recording that the next press stops. */
export class PushToTalk {
  private pressedAt = 0;
  private held = false;
  private ignoreRelease = false;

  constructor(private readonly api: PushToTalkApi) {}

  private stroke() {
    const steps = this.api.binding().trim().split(/\s+/);
    return steps.length === 1 && steps[0] ? steps[0] : null;
  }

  keydown(event: KeyboardEvent): boolean {
    const stroke = this.stroke();
    if (!stroke || !strokeMatches(stroke, event)) return false;
    if (event.repeat) return true;
    this.held = true;
    if (this.api.isRecording()) {
      this.ignoreRelease = true;
      this.api.stop();
    } else {
      this.ignoreRelease = false;
      this.pressedAt = (this.api.now ?? Date.now)();
      this.api.start();
    }
    return true;
  }

  /** Releasing the key, or any modifier in the shortcut, ends the hold: browsers send no keyup for the key once Cmd is up. */
  keyup(event: KeyboardEvent): boolean {
    const stroke = this.stroke();
    if (!this.held || !stroke) return false;
    const parts = stroke.toLowerCase().split('+');
    const key = parts.pop()!;
    const modifier: Record<string, string[]> = {
      Shift: ['shift'],
      Alt: ['alt'],
      Control: ['ctrl', 'mod'],
      Meta: ['meta', 'mod'],
    };
    const released = event.key.toLowerCase() === key || (modifier[event.key] ?? []).some(name => parts.includes(name));
    if (!released) return false;
    this.release();
    return true;
  }

  /** The window lost focus, so the key release will never arrive. */
  blur() {
    if (this.held) this.release();
  }

  private release() {
    this.held = false;
    if (!this.ignoreRelease && (this.api.now ?? Date.now)() - this.pressedAt >= TAP_MS) this.api.stop();
    this.ignoreRelease = false;
  }
}
