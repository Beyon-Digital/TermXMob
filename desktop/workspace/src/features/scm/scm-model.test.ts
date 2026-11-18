import {describe, expect, it} from 'vitest';
import {file} from '../../test-utils/scm';
import {buildBlocks, textLines} from './diff-blocks';
import {diffRef, diffTitle, parseDiffRef} from './diff-ref';
import {changesSignature, groupChanges} from './groups';
import {hunkPatch, isBinaryPatch, newRange, parsePatch, totals} from './patch';

const HEADER = 'diff --git a/a.ts b/a.ts\nindex 1..2 100644\n--- a/a.ts\n+++ b/a.ts\n';

describe('groupChanges', () => {
  it('puts a file with staged and unstaged edits in both groups, each with its own letter', () => {
    const groups = groupChanges([file('a.ts', 'M', 'M'), file('b.ts', 'A', ' '), file('c.ts', ' ', 'D'), file('d.ts', '?', '?'), file('e.ts', 'R', ' ')]);
    expect(groups.staged.map(item => [item.path, item.letter])).toEqual([['a.ts', 'M'], ['b.ts', 'A'], ['e.ts', 'R']]);
    expect(groups.changes.map(item => [item.path, item.letter, item.word])).toEqual([['a.ts', 'M', 'modified'], ['c.ts', 'D', 'deleted'], ['d.ts', 'U', 'untracked']]);
  });

  it('lists a conflicted file once, as a conflict', () => {
    const groups = groupChanges([file('x.ts', 'U', 'U', {status: 'conflict'})]);
    expect(groups.conflicts).toHaveLength(1);
    expect(groups.conflicts[0]).toMatchObject({letter: '!', word: 'conflict', side: 'conflict'});
    expect(groups.staged).toEqual([]);
    expect(groups.changes).toEqual([]);
  });

  it('splits a path into a name and a folder, and signs the list so a change is noticed', () => {
    const [entry] = groupChanges([file('src/lib/a.ts', ' ', 'M')]).changes;
    expect([entry.name, entry.dir]).toEqual(['a.ts', 'src/lib']);
    expect(changesSignature([file('a.ts', ' ', 'M')])).not.toBe(changesSignature([file('a.ts', 'M', ' ')]));
  });
});

describe('diff refs', () => {
  it('round trips a side and a path, including a path with a colon', () => {
    expect(parseDiffRef(diffRef('staged', 'src/a.ts'))).toEqual({side: 'staged', path: 'src/a.ts'});
    expect(parseDiffRef(diffRef('worktree', 'odd:name.ts'))).toEqual({side: 'worktree', path: 'odd:name.ts'});
  });

  it('rejects a ref with no side, no path or another side', () => {
    expect(parseDiffRef(undefined)).toBeNull();
    expect(parseDiffRef('a.ts')).toBeNull();
    expect(parseDiffRef('staged:')).toBeNull();
    expect(parseDiffRef('head:a.ts')).toBeNull();
  });

  it('titles a tab with the file name, and marks the staged side', () => {
    expect(diffTitle('worktree:src/a.ts')).toBe('a.ts');
    expect(diffTitle('staged:src/a.ts')).toBe('a.ts (staged)');
    expect(diffTitle(undefined)).toBe('Changes');
  });
});

describe('parsePatch', () => {
  const patch = HEADER + '@@ -1,2 +1,2 @@ head\n keep\n-old\n+new\n@@ -9 +9,2 @@\n x\n+y\n\\ No newline at end of file\n';

  it('splits the header from the hunks and counts what changed', () => {
    const parsed = parsePatch(patch);
    expect(parsed.headers).toHaveLength(4);
    expect(parsed.hunks).toHaveLength(2);
    expect(parsed.hunks[0]).toMatchObject({oldStart: 1, oldCount: 2, newStart: 1, newCount: 2, heading: 'head', additions: 1, deletions: 1});
    expect(parsed.hunks[1]).toMatchObject({oldStart: 9, oldCount: 1, newStart: 9, newCount: 2, additions: 1, deletions: 0});
    expect(totals(parsed)).toEqual({additions: 2, deletions: 1});
    expect(parsed.hunks[0].lines.map(line => line.kind)).toEqual(['context', 'deletion', 'addition']);
    expect(parsed.hunks[1].lines.map(line => line.kind)).toEqual(['context', 'addition', 'meta']);
  });

  it('builds a patch of one hunk from the header and its own lines, byte for byte', () => {
    const parsed = parsePatch(patch);
    expect(hunkPatch(parsed, parsed.hunks[0])).toBe(HEADER + '@@ -1,2 +1,2 @@ head\n keep\n-old\n+new\n');
  });

  it('stops at a second file in the patch and recognizes binary files', () => {
    const two = HEADER + '@@ -1 +1 @@\n-a\n+b\n' + 'diff --git a/b.ts b/b.ts\n--- a/b.ts\n+++ b/b.ts\n@@ -1 +1 @@\n-c\n+d\n';
    expect(parsePatch(two).hunks).toHaveLength(1);
    expect(isBinaryPatch('Binary files a/x and b/x differ\n')).toBe(true);
    expect(isBinaryPatch('GIT binary patch\nliteral 1\n')).toBe(true);
    expect(isBinaryPatch(patch)).toBe(false);
    expect(parsePatch('').hunks).toEqual([]);
  });

  it('names the new lines a hunk covers, and the line before a pure deletion', () => {
    const parsed = parsePatch(HEADER + '@@ -3,2 +3,0 @@\n-a\n-b\n@@ -10 +10,3 @@\n x\n+y\n+z\n');
    expect(newRange(parsed.hunks[0])).toEqual({from: 3, to: 3});
    expect(newRange(parsed.hunks[1])).toEqual({from: 10, to: 12});
  });
});

describe('buildBlocks', () => {
  const lines = textLines(Array.from({length: 50}, (_, index) => 'l' + (index + 1)).join('\n') + '\n');
  const parsed = parsePatch(HEADER + '@@ -10,2 +10,3 @@\n l10\n+added\n l11\n@@ -30,1 +31,0 @@\n-gone\n');

  it('has no gaps without the file text', () => {
    expect(buildBlocks(parsed, null, {}).map(block => block.type)).toEqual(['hunk', 'hunk']);
  });

  it('adds a gap before, between and after the hunks, with hidden counts', () => {
    const blocks = buildBlocks(parsed, lines, {});
    expect(blocks.map(block => block.type)).toEqual(['gap', 'hunk', 'gap', 'hunk', 'gap']);
    expect(blocks.filter(block => block.type === 'gap').map(block => (block.type === 'gap' ? [block.edge, block.hidden] : []))).toEqual([
      ['top', 9],
      ['between', 19],
      ['bottom', 19],
    ]);
  });

  it('reveals the lines nearest the hunk below, and the lines just after the last hunk', () => {
    const blocks = buildBlocks(parsed, lines, {0: 2, 1: 3, 2: 2});
    const gaps = blocks.filter(block => block.type === 'gap');
    expect(gaps.map(gap => gap.rows.map(row => row.text))).toEqual([['l8', 'l9'], ['l29', 'l30', 'l31'], ['l32', 'l33']]);
    // The first hunk added a line, so below it every old number is the new one minus one; the second only deleted, which keeps the offset.
    expect(gaps[0].rows[0]).toMatchObject({newLine: 8, oldLine: 8});
    expect(gaps[1].rows[0]).toMatchObject({newLine: 29, oldLine: 28});
    expect(gaps[2].rows[0]).toMatchObject({newLine: 32, oldLine: 31});
  });

  it('never reveals more than is hidden', () => {
    const gap = buildBlocks(parsed, lines, {0: 100})[0];
    expect(gap.type === 'gap' && [gap.rows.length, gap.hidden]).toEqual([9, 0]);
  });

  it('reads a file text without the empty line a final newline leaves', () => {
    expect(textLines('a\nb\n')).toEqual(['a', 'b']);
    expect(textLines('a\nb')).toEqual(['a', 'b']);
    expect(textLines('')).toEqual([]);
  });
});
