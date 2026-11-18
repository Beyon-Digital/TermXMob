import type {SessionChangeFile} from '../../lib/session-changes';
import type {ScmEntry} from './scm-types';

const WORDS: Record<string, string> = {
  M: 'modified',
  A: 'added',
  D: 'deleted',
  R: 'renamed',
  C: 'copied',
  U: 'untracked',
  '!': 'conflict',
};

/** The letter for one side of a file: the git status character, with `?` shown as U and a type change as M. */
function letterOf(code: string): string {
  if (code === '?') return 'U';
  if (code === 'T') return 'M';
  return code;
}

function entry(file: SessionChangeFile, side: ScmEntry['side'], code: string): ScmEntry {
  const letter = side === 'conflict' ? '!' : letterOf(code);
  const parts = file.path.split('/');
  return {
    key: side + ':' + file.path,
    path: file.path,
    name: parts.at(-1) ?? file.path,
    dir: parts.slice(0, -1).join('/'),
    side,
    letter,
    word: WORDS[letter] ?? 'changed',
    file,
  };
}

export type Groups = {conflicts: ScmEntry[]; staged: ScmEntry[]; changes: ScmEntry[]};

/**
 * Split the changed files the way every git client does. A file with both staged and unstaged edits is listed in both groups,
 * each with its own letter; a conflicted file is listed once, under Merge conflicts.
 */
export function groupChanges(files: readonly SessionChangeFile[]): Groups {
  const groups: Groups = {conflicts: [], staged: [], changes: []};
  for (const file of files) {
    if (file.status === 'conflict') {
      groups.conflicts.push(entry(file, 'conflict', '!'));
      continue;
    }
    if (file.staged) groups.staged.push(entry(file, 'staged', file.index));
    if (file.untracked || file.worktree.trim()) groups.changes.push(entry(file, 'worktree', file.untracked ? '?' : file.worktree));
  }
  return groups;
}

/** A stable signature of the changed files, to notice when the list moved. */
export function changesSignature(files: readonly SessionChangeFile[]): string {
  return files.map(file => file.path + ':' + file.index + file.worktree).join('|');
}
