/** Design review (independent reviewer). FAILS on the composed branch and proves the finding; fix the code, not the test. */
import {readFileSync} from 'node:fs';
import {expect, it} from 'vitest';

/** Text of every @media block that targets coarse pointers. */
function coarseBlocks(css: string) {
  let result = '', index = 0;
  for (;;) {
    const start = css.indexOf('@media(pointer:coarse)', index);
    if (start < 0) return result;
    const open = css.indexOf('{', start);
    let depth = 1, cursor = open + 1;
    while (depth && cursor < css.length) {depth += css[cursor] === '{' ? 1 : css[cursor] === '}' ? -1 : 0; cursor++;}
    result += css.slice(open + 1, cursor - 1) + '\n';
    index = cursor;
  }
}

it('applies the 44px coarse-pointer sizes to controls inside portalled popovers and the Advanced panel', () => {
  // Chrome 375 with (pointer: coarse): filter popover Apply 64x32, its select 40, Combine "Add" 74x32, "Add table" 89x32,
  // Advanced panel Close 44x32, "Save query" 123x32 and "Run" 76x32. The coarse rules in data-studio.css are scoped under
  // .data-studio/.data-navigation/.data-dialog, which do not contain Radix portals, so they never match.
  const css = ['data-studio.css', 'data-query.css'].map(name => readFileSync(new URL('./' + name, import.meta.url), 'utf8')).join('\n');
  expect(coarseBlocks(css)).toMatch(/\.ui-popover|\.data-advanced-panel/);
});
