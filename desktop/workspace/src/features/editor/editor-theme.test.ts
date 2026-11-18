import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';
import {editorChromeSpec, editorHighlightSpec, editorTheme} from './editor-theme';

const tokens = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8');
const defined = new Set([...tokens.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));

function leaves(value: unknown, path: string[] = [], out: {path: string; value: string}[] = []) {
  if (typeof value === 'string') out.push({path: path.join(' > '), value});
  else if (Array.isArray(value)) value.forEach((item, index) => leaves(item, [...path, String(index)], out));
  else if (value && typeof value === 'object') for (const [key, item] of Object.entries(value)) leaves(item, [...path, key], out);
  return out;
}

const spec = [...leaves(editorChromeSpec), ...leaves(editorHighlightSpec.map(({tag, ...rest}) => rest))];
const LITERAL_COLOUR = /#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(/i;
const COLOUR_PROPERTY = /(^|[ >])(color|backgroundColor|borderColor|borderLeftColor|caretColor|outline|border|borderTop|borderBottom|boxShadow)$/;

describe('editor theme', () => {
  it('contains no literal colour, so light and dark need no reconfiguration', () => {
    expect(spec.filter(({value}) => LITERAL_COLOUR.test(value))).toEqual([]);
  });

  it('draws every colour from a CSS variable', () => {
    const bare = spec.filter(({path, value}) => COLOUR_PROPERTY.test(path) && !/var\(--/.test(value) && !/^(none|transparent|inherit)$/.test(value));
    expect(bare).toEqual([]);
  });

  it('only references variables that tokens.css defines', () => {
    const referenced = new Set(spec.flatMap(({value}) => [...value.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1])));
    expect([...referenced].filter((name) => !defined.has(name))).toEqual([]);
  });

  it('uses the syntax, selection, diff and surface tokens it promises', () => {
    const used = new Set(spec.flatMap(({value}) => [...value.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1])));
    for (const name of ['--bg', '--text', '--text-2', '--text-3', '--surface-2', '--accent', '--selection', '--diff-add', '--diff-del', '--syntax-keyword', '--syntax-string', '--syntax-constant', '--syntax-entity', '--syntax-comment', '--syntax-punctuation']) expect(used.has(name), name).toBe(true);
  });

  it('sets the code font to the 14/20 tokens and the mono family', () => {
    expect(editorChromeSpec['&'].fontSize).toBe('var(--code-size)');
    expect(editorChromeSpec['.cm-scroller'].lineHeight).toBe('var(--code-line)');
    expect(editorChromeSpec['.cm-scroller'].fontFamily).toBe('var(--font-mono)');
  });

  it('keeps weights within DESIGN.md and uses no gradient', () => {
    expect(spec.filter(({path, value}) => /fontWeight$/.test(path) && Number(value) > 600)).toEqual([]);
    expect(spec.filter(({value}) => /gradient/.test(value))).toEqual([]);
  });

  it('is a stable extension list', () => {
    expect(editorTheme()).toHaveLength(2);
  });
});
