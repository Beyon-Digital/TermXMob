import {readFileSync} from 'node:fs';
import {describe, expect, it} from 'vitest';

const files = ['data-studio.css', 'data-query.css', 'bi-studio.css'] as const;
const source = (name: string) => readFileSync(new URL('./' + name, import.meta.url), 'utf8');
/** Removes every @media block that targets coarse pointers, where 44px touch targets belong. */
function withoutCoarseBlocks(css: string) {
  let result = '', index = 0;
  while (index < css.length) {
    const start = css.indexOf('@media', index);
    if (start < 0) {result += css.slice(index); break;}
    result += css.slice(index, start);
    const open = css.indexOf('{', start), query = css.slice(start, open);
    let depth = 1, cursor = open + 1;
    while (depth && cursor < css.length) {depth += css[cursor] === '{' ? 1 : css[cursor] === '}' ? -1 : 0; cursor++;}
    if (!query.includes('pointer:coarse')) result += css.slice(start, cursor);
    index = cursor;
  }
  return result;
}

describe.each(files)('%s follows the shared design rules', name => {
  const css = source(name);
  it('has no !important overrides', () => expect(css).not.toMatch(/!\s*important/));
  it('forces 44px targets only under pointer: coarse', () => expect(withoutCoarseBlocks(css)).not.toMatch(/(?:min-)?(?:height|width):\s*44px/));
  it('has no gradients, blur or hex colours', () => {
    expect(css).not.toMatch(/gradient\(/);
    expect(css).not.toMatch(/backdrop-filter|filter:\s*blur/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
  it('does not redefine the shared tokens', () => expect(css).not.toMatch(/(?:^|[{;])\s*--(?:bg|surface|surface-2|border|border-strong|text|text-2|text-3|accent|danger|success):/m));
});

describe('data-studio.css', () => {
  const css = source('data-studio.css');
  it('drops the hand-built Copilot resizer', () => expect(css).not.toContain('.data-resize'));
  it('does not recolour the primary button', () => expect(css).not.toMatch(/\.button-primary\s*\{[^}]*background/));
  it('keeps secondary controls bordered', () => expect(css).not.toMatch(/border-color:\s*transparent/));
});
