import {readFileSync} from 'node:fs';
import {expect, it} from 'vitest';

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8');

// jsdom does not run the cascade. The real-browser measurements for these rules (375x640, 375x800, 1280x800) are in the fix pass notes;
// this guards the rules that keep the composer toolbar reachable while the live voice panel is open.
it('lets the live panel give way before the message box and keeps its transcript list scrollable', () => {
  const css = read('./live-voice.css');
  expect(css).toMatch(/\.live-voice-panel\{flex:0 100 auto\}/);
  const lines = css.split('\n').find(line => line.startsWith('.live-voice-lines{'));
  expect(lines).toContain('min-height:0');
  expect(lines).toContain('overflow-y:auto');
  expect(css).toContain('.chat-region .composer-container:has(.live-voice-panel.is-live)');
});

it('collapses a running session to one compact bar on small or short screens, with 44px icon buttons', () => {
  const css = read('./live-voice.css');
  const compact = css.slice(css.indexOf('@media(max-width:480px),(max-height:700px)'));
  expect(compact).toContain('.live-voice-panel.is-live{flex-direction:row');
  expect(compact).toContain('.live-voice-lines,.live-voice-panel.is-live .live-voice-hint{display:none}');
  expect(compact).toContain('min-width:44px;min-height:44px');
  expect(compact).toContain('.live-voice-label{position:absolute');
});

it('does not rely on the old fixed-height panel that clipped the toolbar', () => {
  expect(read('./live-voice.css')).not.toContain('max-height:max(110px,22dvh)');
});
