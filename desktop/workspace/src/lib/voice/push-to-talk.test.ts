import {expect, it, vi} from 'vitest';
import {defaults, labels} from '../keybindings';
import {PushToTalk, TAP_MS} from './push-to-talk';

function setup(binding = 'Mod+Shift+M') {
  let clock = 1000;
  const state = {recording: false};
  const api = {
    binding: () => binding,
    isRecording: () => state.recording,
    start: vi.fn(() => void (state.recording = true)),
    stop: vi.fn(() => void (state.recording = false)),
    now: () => clock,
  };
  return {talk: new PushToTalk(api), api, advance: (ms: number) => (clock += ms)};
}
const down = (init: KeyboardEventInit = {}) =>
  new KeyboardEvent('keydown', {key: 'M', ctrlKey: true, shiftKey: true, ...init});
const up = (init: KeyboardEventInit = {}) => new KeyboardEvent('keyup', {key: 'm', ...init});

it('has a default binding with a readable label', () => {
  expect(defaults.voiceTalk).toBe('Mod+Shift+M');
  expect(labels.voiceTalk).toMatch(/talk/i);
});

it('records while the shortcut is held and stops on release', () => {
  const {talk, api, advance} = setup();
  expect(talk.keydown(down())).toBe(true);
  expect(api.start).toHaveBeenCalledOnce();
  expect(talk.keydown(down({repeat: true}))).toBe(true);
  expect(api.start).toHaveBeenCalledOnce();
  advance(TAP_MS + 100);
  expect(talk.keyup(up())).toBe(true);
  expect(api.stop).toHaveBeenCalledOnce();
});

it('keeps recording after a quick tap and stops on the next press', () => {
  const {talk, api, advance} = setup();
  talk.keydown(down());
  advance(100);
  talk.keyup(up());
  expect(api.stop).not.toHaveBeenCalled();
  advance(2000);
  talk.keydown(down());
  expect(api.stop).toHaveBeenCalledOnce();
  talk.keyup(up());
  expect(api.start).toHaveBeenCalledOnce();
});

it('ignores other keys, other bindings and multi-step chords', () => {
  const {talk, api} = setup();
  expect(talk.keydown(new KeyboardEvent('keydown', {key: 'k', ctrlKey: true}))).toBe(false);
  expect(talk.keyup(up())).toBe(false);
  const chord = setup('Mod+K Z');
  expect(chord.talk.keydown(down())).toBe(false);
  expect(api.start).not.toHaveBeenCalled();
  expect(chord.api.start).not.toHaveBeenCalled();
});

it('stops when the modifier is released before the key, as browsers on macOS report it', () => {
  const {talk, api, advance} = setup();
  talk.keydown(down());
  advance(TAP_MS + 100);
  expect(talk.keyup(new KeyboardEvent('keyup', {key: 'Control'}))).toBe(true);
  expect(api.stop).toHaveBeenCalledOnce();
  expect(talk.keyup(up())).toBe(false);
  expect(api.stop).toHaveBeenCalledOnce();
});

it('ignores a modifier that is not part of the shortcut', () => {
  const {talk, api, advance} = setup();
  talk.keydown(down());
  advance(TAP_MS + 100);
  expect(talk.keyup(new KeyboardEvent('keyup', {key: 'Alt'}))).toBe(false);
  expect(api.stop).not.toHaveBeenCalled();
});

it('stops a hold when the window loses focus', () => {
  const {talk, api, advance} = setup();
  talk.keydown(down());
  advance(TAP_MS + 100);
  talk.blur();
  expect(api.stop).toHaveBeenCalledOnce();
  talk.blur();
  expect(api.stop).toHaveBeenCalledOnce();
});
