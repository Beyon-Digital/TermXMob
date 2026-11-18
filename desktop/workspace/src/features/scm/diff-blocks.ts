import type {ReviewDiffLine} from '../../lib/change-diff';
import type {FilePatch, Hunk} from './patch';

/**
 * The diff as blocks: each hunk, and between them the unchanged lines the patch leaves out. When the file's current text is
 * known (the unstaged side), a gap can be expanded a step at a time; the old line number of a revealed line is its new number
 * plus the offset the previous hunk left. Without the text there are no gaps, only hunks.
 */

export const EXPAND_STEP = 20;

export type GapBlock = {type: 'gap'; gap: number; rows: ReviewDiffLine[]; hidden: number; edge: 'top' | 'between' | 'bottom'};
export type HunkBlock = {type: 'hunk'; hunk: Hunk};
export type Block = GapBlock | HunkBlock;

const endNew = (hunk: Hunk) => (hunk.newCount > 0 ? hunk.newStart + hunk.newCount - 1 : hunk.newStart);
const endOld = (hunk: Hunk) => (hunk.oldCount > 0 ? hunk.oldStart + hunk.oldCount - 1 : hunk.oldStart);
const startNewBefore = (hunk: Hunk) => (hunk.newCount > 0 ? hunk.newStart - 1 : hunk.newStart);

/** The lines of a file's text, without the empty string a final newline leaves. */
export function textLines(text: string): string[] {
  const lines = text.split('\n');
  if (lines.at(-1) === '') lines.pop();
  return lines;
}

function gapBlock(file: FilePatch, gap: number, lines: string[], revealed: number): GapBlock | null {
  const {hunks} = file;
  const previous = hunks[gap - 1];
  const next = hunks[gap];
  const from = previous ? endNew(previous) + 1 : 1;
  const to = next ? startNewBefore(next) : lines.length;
  const count = to - from + 1;
  if (count <= 0) return null;
  const delta = previous ? endOld(previous) - endNew(previous) : 0;
  const shown = Math.min(Math.max(revealed, 0), count);
  // Above a hunk the nearest lines are the ones just before it; below the last hunk they are the ones just after it.
  const first = next ? to - shown + 1 : from;
  const rows: ReviewDiffLine[] = [];
  for (let line = first; line < first + shown; line++) {
    rows.push({kind: 'context', text: lines[line - 1] ?? '', newLine: line, oldLine: line + delta});
  }
  return {type: 'gap', gap, rows, hidden: count - shown, edge: !previous ? 'top' : !next ? 'bottom' : 'between'};
}

export function buildBlocks(file: FilePatch, lines: string[] | null, revealed: Record<number, number>): Block[] {
  const blocks: Block[] = [];
  file.hunks.forEach((hunk, index) => {
    const gap = lines ? gapBlock(file, index, lines, revealed[index] ?? 0) : null;
    if (gap) blocks.push(gap);
    blocks.push({type: 'hunk', hunk});
  });
  const tail = lines && file.hunks.length ? gapBlock(file, file.hunks.length, lines, revealed[file.hunks.length] ?? 0) : null;
  if (tail) blocks.push(tail);
  return blocks;
}
