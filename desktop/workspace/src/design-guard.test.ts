import {existsSync, readdirSync, readFileSync, statSync, writeFileSync} from 'node:fs';
import {join, relative, resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

/* Design guard: a per-file ratchet over the DESIGN.md banned list.
   - Every metric below is counted per source file (CSS, TS and TSX, tests excluded).
   - src/design-budget.json holds the allowed count per file and metric. A count may fall, never rise,
     and a file that is not in the budget must be at zero.
   - After removing violations, tighten the budget:  UPDATE_DESIGN_BUDGET=1 pnpm --dir desktop/workspace test design-guard
   - The hex metric skips src/styles/tokens.css, the one place colours are defined.
   Counts are text heuristics: they hold the line, they do not prove a stylesheet is compliant. */

const SRC = resolve(process.cwd(), 'src');
const BUDGET_PATH = join(SRC, 'design-budget.json');
const TOKENS = 'src/styles/tokens.css';

const SCALE_SPACE = new Set([0, 4, 8, 12, 16, 24, 32, 48, 64]);
const SCALE_FONT = new Set([12, 14, 16, 18, 20, 24, 32]);
const SCALE_RADIUS_PX = new Set([0, 8, 12, 999, 9999, 99999]);
const SCALE_RADIUS_PERCENT = new Set([0, 50, 100]);

export const METRICS = ['hex', 'important', 'fontSize', 'radius', 'spacing', 'outlineNone', 'gradient', 'backdropFilter', 'fontWeight', 'uppercase'] as const;
type Metric = typeof METRICS[number];
type Counts = Partial<Record<Metric, number>>;

const isTest = (path: string) => /\.test\.tsx?$/.test(path) || /(^|\/)(test-setup|test-axe)\.ts$/.test(path) || /(^|\/)test-utils\//.test(path);

function walk(directory: string, out: string[] = []): string[] {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(css|tsx?)$/.test(entry)) out.push(full);
  }
  return out;
}

const all = (text: string, pattern: RegExp) => [...text.matchAll(pattern)];
const px = (value: string) => all(value, /(?<![\w.#-])-?\d*\.?\d+px\b/g).map((m) => Math.abs(parseFloat(m[0])));

function offScaleFontSize(value: string) {
  const v = value.replace(/!important/g, '').trim();
  if (/^(inherit|initial|unset|revert|0)$/.test(v) || v.startsWith('var(')) return false;
  const match = v.match(/^(\d*\.?\d+)px$/);
  return !(match && SCALE_FONT.has(parseFloat(match[1])));
}
function offScaleRadius(value: string) {
  const v = value.replace(/!important/g, '').trim();
  if (/^(inherit|initial|unset|revert)$/.test(v)) return false;
  const stripped = v.replace(/var\([^)]*\)/g, ' ');
  return all(stripped, /-?\d*\.?\d+(px|%|em|rem)?/g).some((m) => {
    const number = Math.abs(parseFloat(m[0]));
    if (m[1] === '%') return !SCALE_RADIUS_PERCENT.has(number);
    if (m[1] === 'px') return !SCALE_RADIUS_PX.has(number);
    return number !== 0; // em, rem and unitless values are off the scale
  });
}

export function countCss(raw: string, countHex: boolean): Counts {
  const text = raw.replace(/\/\*[\s\S]*?\*\//g, '');
  const counts: Counts = {};
  const add = (metric: Metric, amount: number) => { if (amount) counts[metric] = (counts[metric] ?? 0) + amount; };
  if (countHex) add('hex', all(text, /(?<![\w&#-])#[0-9a-fA-F]{3,8}(?![\w-])/g).length);
  add('important', all(text, /!important/g).length);
  add('fontSize', all(text, /(?<![\w-])font-size\s*:\s*([^;}]+)/g).filter((m) => offScaleFontSize(m[1])).length);
  add('radius', all(text, /(?<![\w-])border(?:-(?:top|bottom|start|end)-(?:left|right|start|end))?-radius\s*:\s*([^;}]+)/g).filter((m) => offScaleRadius(m[1])).length);
  add('spacing', all(text, /(?<![\w-])(?:margin|padding)(?:-(?:top|right|bottom|left|inline|block)(?:-(?:start|end))?)?\s*:\s*([^;}]+)|(?<![\w-])(?:row-|column-)?gap\s*:\s*([^;}]+)/g)
    .filter((m) => px(m[1] ?? m[2] ?? '').some((n) => !SCALE_SPACE.has(n))).length);
  add('outlineNone', all(text, /(?<![\w-])outline\s*:\s*(?:none|0)(?![\w.-])|(?<![\w-])outline-style\s*:\s*none/g).length);
  add('gradient', all(text, /(?<![\w-])(?:repeating-)?(?:linear|radial|conic)-gradient\(/g).length);
  add('backdropFilter', all(text, /(?<![\w-])(?:-webkit-)?backdrop-filter\s*:/g).length);
  add('fontWeight', all(text, /(?<![\w-])font-weight\s*:\s*([^;}!]+)/g).filter((m) => /^(bold|bolder)$/.test(m[1].trim()) || parseFloat(m[1]) > 600).length);
  add('uppercase', all(text, /(?<![\w-])text-transform\s*:\s*(?:uppercase|capitalize)/g).length);
  return counts;
}

export function countScript(raw: string): Counts {
  // Drop block comments and whole-line comments so prose about a rule is not counted as using it.
  const text = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const counts: Counts = {};
  const add = (metric: Metric, amount: number) => { if (amount) counts[metric] = (counts[metric] ?? 0) + amount; };
  add('hex', all(text, /(?<![\w&#-])#[0-9a-fA-F]{3,8}(?![\w-])/g).length);
  add('important', all(text, /!important/g).length);
  add('fontSize', all(text, /\bfontSize\s*:\s*(\d+(?:\.\d+)?)\b/g).filter((m) => !SCALE_FONT.has(parseFloat(m[1]))).length);
  add('radius', all(text, /\bborderRadius\s*:\s*(\d+(?:\.\d+)?)\b/g).filter((m) => !SCALE_RADIUS_PX.has(parseFloat(m[1]))).length);
  add('spacing', all(text, /\b(?:margin|padding)(?:Top|Right|Bottom|Left|Inline|Block)?\s*:\s*(\d+(?:\.\d+)?)\b|\b(?:row|column)?[gG]ap\s*:\s*(\d+(?:\.\d+)?)\b/g)
    .filter((m) => !SCALE_SPACE.has(parseFloat(m[1] ?? m[2]))).length);
  add('outlineNone', all(text, /\boutline\s*:\s*['"`]?(?:none|0)\b/g).length);
  add('gradient', all(text, /(?:repeating-)?(?:linear|radial|conic)-gradient\(/g).length);
  add('backdropFilter', all(text, /\bbackdrop-?[fF]ilter\b/g).length);
  add('fontWeight', all(text, /\bfontWeight\s*:\s*['"`]?(\w+)/g).filter((m) => /^(bold|bolder)$/.test(m[1]) || parseFloat(m[1]) > 600).length);
  add('uppercase', all(text, /\btextTransform\s*:\s*['"`](?:uppercase|capitalize)/g).length);
  return counts;
}

export function collect(): Record<string, Counts> {
  const result: Record<string, Counts> = {};
  for (const file of walk(SRC)) {
    const path = 'src/' + relative(SRC, file).split('\\').join('/');
    if (isTest(path)) continue;
    const text = readFileSync(file, 'utf8');
    const counts = path.endsWith('.css') ? countCss(text, path !== TOKENS) : countScript(text);
    if (Object.keys(counts).length) result[path] = counts;
  }
  return result;
}

type Budget = {files: Record<string, Counts>};
const loadBudget = (): Budget => existsSync(BUDGET_PATH) ? JSON.parse(readFileSync(BUDGET_PATH, 'utf8')) : {files: {}};

function writeBudget(files: Record<string, Counts>) {
  const lines = Object.keys(files).sort().map((path) => `    ${JSON.stringify(path)}: {${METRICS.filter((m) => files[path][m]).map((m) => `"${m}": ${files[path][m]}`).join(', ')}}`);
  const header = '  "note": "Per-file ratchet for src/design-guard.test.ts. Counts may fall, never rise; new files start at zero. Tighten with UPDATE_DESIGN_BUDGET=1.",\n  "metrics": ' + JSON.stringify(METRICS) + ',\n';
  writeFileSync(BUDGET_PATH, '{\n' + header + '  "files": {\n' + lines.join(',\n') + '\n  }\n}\n');
}

describe('design guard', () => {
  const current = collect();

  if (process.env.UPDATE_DESIGN_BUDGET) {
    it('rewrites design-budget.json from the current counts', () => {
      writeBudget(current);
      expect(existsSync(BUDGET_PATH)).toBe(true);
    });
    return;
  }

  it('has a budget file', () => {
    expect(existsSync(BUDGET_PATH)).toBe(true);
  });

  it('no file exceeds its budget and new files start at zero', () => {
    const budget = loadBudget().files;
    const violations: string[] = [];
    for (const [path, counts] of Object.entries(current)) {
      for (const metric of METRICS) {
        const count = counts[metric] ?? 0;
        const allowed = budget[path]?.[metric] ?? 0;
        if (count > allowed) violations.push(`${path}: ${metric} ${count} > budget ${allowed}${budget[path] ? '' : ' (new file or not in budget)'}`);
      }
    }
    expect(violations, 'DESIGN.md banned or off-scale values added. Use tokens from src/styles/tokens.css.\n').toEqual([]);
  });

  it('ignores files that were deleted', () => {
    const stale = Object.keys(loadBudget().files).filter((path) => !existsSync(resolve(process.cwd(), path)));
    expect(stale, 'Remove deleted files from design-budget.json (UPDATE_DESIGN_BUDGET=1).').toEqual([]);
  });

  it('counts what it claims to count', () => {
    expect(countCss('a{color:#abc;font-size:13px;border-radius:6px;padding:10px 8px;outline:none;background:linear-gradient(red,blue);backdrop-filter:blur(2px);font-weight:700;text-transform:uppercase;margin:0 !important}', true))
      .toEqual({hex: 1, important: 1, fontSize: 1, radius: 1, spacing: 1, outlineNone: 1, gradient: 1, backdropFilter: 1, fontWeight: 1, uppercase: 1});
    expect(countCss('#root{font-size:var(--text-sm);border-radius:var(--radius);padding:var(--space-8) 12px;gap:4px;outline:2px solid var(--accent);font-weight:600;border-radius:50%}', true)).toEqual({});
    expect(countCss('a{color:#abc}', false)).toEqual({});
    expect(countScript("const s={fontSize:13,borderRadius:8,padding:10,textTransform:'uppercase',color:'#fff'}")).toEqual({hex: 1, fontSize: 1, spacing: 1, uppercase: 1});
  });
});
