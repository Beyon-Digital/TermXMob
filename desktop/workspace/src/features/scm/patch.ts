import {parseReviewDiff, type ReviewDiffLine} from '../../lib/change-diff';

/**
 * A unified patch for one file, split into its header and hunks. The host returns patches, not file contents, so everything
 * the diff tab shows (and every hunk it stages) is built from this.
 */

export type Hunk = {
  index: number;
  /** The `@@ -a,b +c,d @@ heading` line. */
  header: string;
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  /** What follows the second `@@`: usually the enclosing function. */
  heading: string;
  /** The hunk's own lines, header first, exactly as the patch has them. */
  raw: string[];
  lines: ReviewDiffLine[];
  additions: number;
  deletions: number;
};

export type FilePatch = {headers: string[]; hunks: Hunk[]; binary: boolean};

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$/;

export function isBinaryPatch(patch: string): boolean {
  return /^Binary files .* differ$/m.test(patch) || patch.includes('GIT binary patch');
}

export function parsePatch(patch: string): FilePatch {
  const lines = patch.replace(/\r\n/g, '\n').split('\n');
  if (lines.at(-1) === '') lines.pop();
  const headers: string[] = [];
  const raws: string[][] = [];
  let seenFile = false;
  for (const line of lines) {
    if (line.startsWith('diff --git ')) {
      if (seenFile && raws.length) break;
      seenFile = true;
    }
    if (HUNK.test(line)) raws.push([line]);
    else if (raws.length) raws.at(-1)!.push(line);
    else headers.push(line);
  }
  const hunks = raws.map((raw, index): Hunk => {
    const match = HUNK.exec(raw[0])!;
    const rows = parseReviewDiff(raw.join('\n')).filter(row => row.kind !== 'hunk');
    return {
      index,
      header: raw[0],
      oldStart: Number(match[1]),
      oldCount: match[2] === undefined ? 1 : Number(match[2]),
      newStart: Number(match[3]),
      newCount: match[4] === undefined ? 1 : Number(match[4]),
      heading: match[5],
      raw,
      lines: rows,
      additions: rows.filter(row => row.kind === 'addition').length,
      deletions: rows.filter(row => row.kind === 'deletion').length,
    };
  });
  return {headers, hunks, binary: isBinaryPatch(patch)};
}

/** A patch of one hunk of the file, which the host can apply to the index (or reverse out of it). */
export function hunkPatch(file: FilePatch, hunk: Hunk): string {
  return [...file.headers, ...hunk.raw].join('\n') + '\n';
}

export function totals(file: FilePatch): {additions: number; deletions: number} {
  return {
    additions: file.hunks.reduce((sum, hunk) => sum + hunk.additions, 0),
    deletions: file.hunks.reduce((sum, hunk) => sum + hunk.deletions, 0),
  };
}

/** The new-file line range a hunk covers, for its label. A hunk that only deletes covers no new lines, so it names the line before. */
export function newRange(hunk: Hunk): {from: number; to: number} {
  return hunk.newCount > 0 ? {from: hunk.newStart, to: hunk.newStart + hunk.newCount - 1} : {from: hunk.newStart, to: hunk.newStart};
}
