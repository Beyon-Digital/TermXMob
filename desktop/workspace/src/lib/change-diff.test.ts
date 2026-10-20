import {describe,it,expect} from 'vitest';
import {parseReviewDiff,splitReviewDiff} from './change-diff';
describe('review diff presentation',()=>{
 it('keeps real before/after line numbers across hunks and preserves content beginning with diff signs',()=>{
  const lines=parseReviewDiff('diff --git a/code.ts b/code.ts\n--- a/code.ts\n+++ b/code.ts\n@@ -4,2 +4,3 @@\n context\n-old\n++++literal\n+new\n@@ -20 +21 @@\n-before\n+after\n\\ No newline at end of file\n');
  expect(lines.filter(row=>row.kind==='deletion').map(row=>row.oldLine)).toEqual([5,20]);
  expect(lines.filter(row=>row.kind==='addition').map(row=>[row.text,row.newLine])).toEqual([['+++literal',5],['new',6],['after',21]]);
  expect(lines.at(-1)?.kind).toBe('meta');
 });
 it('aligns replacements and leaves unmatched lines blank in split view',()=>{
  const split=splitReviewDiff(parseReviewDiff('@@ -1,2 +1,3 @@\n-old\n+first\n+second\n shared\n'));
  expect(split.map(row=>[row.kind,row.before?.text,row.after?.text])).toEqual([['hunk',undefined,undefined],['line','old','first'],['line',undefined,'second'],['line','shared','shared']]);
 });
});
