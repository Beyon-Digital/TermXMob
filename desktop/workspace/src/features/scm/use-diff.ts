import {useCallback, useEffect, useRef, useState} from 'react';
import {request, type Project, type Session} from '../../lib/api';
import {sessionChangesPath} from '../../lib/session-changes';
import {readFile} from '../editor/file-io';
import {textLines} from './diff-blocks';
import {isBinaryPatch, parsePatch, type FilePatch} from './patch';
import {readDeliveryDiff} from './scm-api';
import {onScmChanged} from './scm-bus';
import type {Side} from './scm-types';

/** A patch longer than this is not drawn: the host does not cap the staged and unstaged reads, so the tab does. */
export const MAX_PATCH_CHARS = 1_000_000;

export type DiffData =
  | {status: 'loading'}
  | {status: 'error'; message: string}
  | {
      status: 'ready';
      file: FilePatch;
      /** The file is new to Git: its patch is the whole file, and there is no index to stage a hunk into. */
      untracked: boolean;
      binary: boolean;
      tooLarge: boolean;
      /** The file's current text, when the unstaged side can expand its context. */
      lines: string[] | null;
    };

type Options = {project: Project | null; session: Session | null; side: Side; path: string; visible: boolean};

const EMPTY: FilePatch = {headers: [], hunks: [], binary: false};

/**
 * One file's patch for one side. The unstaged side reads the index-to-working-tree patch; if that is empty the file may be
 * untracked, so the checkout review's patch against HEAD stands in (a whole-file addition). It reloads when source control
 * reports a change and when its tab becomes visible.
 */
export function useDiff({project, session, side, path, visible}: Options) {
  const [data, setData] = useState<DiffData>({status: 'loading'});
  const [tick, setTick] = useState(0);
  const epoch = useRef(0);
  const worktreeId = session?.worktree_id || '';
  useEffect(() => onScmChanged(() => setTick(value => value + 1)), []);
  useEffect(() => setData({status: 'loading'}), [project?.id, worktreeId, side, path]);

  const load = useCallback(async () => {
    if (!project || !session) return;
    const run = ++epoch.current;
    const current = () => epoch.current === run;
    try {
      let patch = await readDeliveryDiff(project, worktreeId, path, side === 'staged');
      let untracked = false;
      let truncated = false;
      let binary = isBinaryPatch(patch);
      if (!patch && side === 'worktree') {
        const review = await request<{diff: string; binary: boolean; truncated: boolean}>(sessionChangesPath(session, path));
        patch = review.diff;
        untracked = !!review.diff;
        binary = review.binary;
        truncated = review.truncated;
      }
      if (!current()) return;
      const tooLarge = truncated || patch.length > MAX_PATCH_CHARS;
      const file = binary || tooLarge ? EMPTY : parsePatch(patch);
      setData({status: 'ready', file, untracked, binary, tooLarge, lines: null});
      if (side !== 'worktree' || untracked || binary || tooLarge || !file.hunks.length) return;
      try {
        const content = await readFile({project, session}, path);
        if (current()) setData(previous => (previous.status === 'ready' ? {...previous, lines: textLines(content.content)} : previous));
      } catch {
        // The diff stands without expandable context.
      }
    } catch (reason) {
      if (current()) setData({status: 'error', message: reason instanceof Error ? reason.message : String(reason)});
    }
  }, [project?.id, session?.id, worktreeId, side, path]);

  useEffect(() => {
    if (visible) void load();
    return () => {
      epoch.current++;
    };
  }, [load, visible, tick]);

  return {data, reload: load};
}
