import {readFileSync} from 'node:fs';
import {resolve as resolvePath} from 'node:path';
import {describe, expect, it} from 'vitest';

// Source-level guards for two regressions the UI audit found: a wrapper that draws the focus ring with
// :focus-within also showed the inner control's own ring (two rings), and views that mount on a tab, sidebar or
// settings switch popped in with no entrance. The rendered behaviour is checked by the Playwright audit; these keep
// the rules from being deleted or narrowed.
const read = (file: string) => readFileSync(resolvePath(process.cwd(), 'src', file), 'utf8');
const WORKSPACES = ['agent', 'studio', 'data'];

describe('focus rings', () => {
  const theme = read('agent-theme.css');

  it('silences the inner control when a wrapper owns the ring', () => {
    const rule = theme.match(/:is\(\.describe-composer, \.control-search\):focus-within :is\(input, textarea, select\):focus-visible \{([^}]*)\}/);
    expect(rule, 'wrapper-owned ring rule is missing').not.toBeNull();
    // transparent, not none: forced-colors mode must still draw something
    expect(rule![1]).toMatch(/outline-color:\s*transparent/);
  });

  it('keeps the wrappers drawing their own ring', () => {
    expect(read('features/authoring/authoring.css')).toMatch(/\.describe-composer:focus-within\s*\{[^}]*outline:\s*2px solid var\(--accent\)/);
    expect(read('features/control-plane.css')).toMatch(/\.control-search:focus-within\s*\{[^}]*outline:\s*2px solid/);
  });
});

describe('motion', () => {
  const motion = read('workspace-motion.css');

  it('applies state transitions and reduced-motion to every workspace', () => {
    for (const workspace of WORKSPACES) {
      expect(motion, `transition selector misses ${workspace}`).toContain(`[data-app-workspace=${workspace}]`);
    }
    const reduced = motion.slice(motion.indexOf('@media(prefers-reduced-motion:reduce)'));
    for (const workspace of WORKSPACES) expect(reduced, `reduced-motion misses ${workspace}`).toContain(`[data-app-workspace=${workspace}]`);
  });

  it('fades in mounted views, panels and toasts using only opacity and translate', () => {
    for (const selector of ['.tab-content', '.settings-actions', '.explorer-view', '.search-view', '.toast']) {
      expect(motion, `${selector} has no entrance`).toContain(selector);
    }
    expect(motion).toMatch(/@keyframes workspace-view-in\{from\{opacity:0\}to\{opacity:1\}\}/);
  });
});
