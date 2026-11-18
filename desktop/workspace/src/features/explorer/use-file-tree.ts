import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import type {Project, Session} from '../../lib/api';
import {isDirectory, listTree, type FileEntry} from '../editor/file-io';

export type TreeRow = {entry: FileEntry; depth: number; directory: boolean; expanded: boolean; name: string};

const byKind = (a: FileEntry, b: FileEntry) =>
  Number(isDirectory(b)) - Number(isDirectory(a)) || (a.name || a.path).localeCompare(b.name || b.path);

/**
 * The folders and files of a checkout, loaded one folder at a time as it is expanded. It starts over whenever the project,
 * the conversation or its checkout changes, and a late answer for an earlier target is dropped.
 */
export function useFileTree(project: Project | null, session: Session | null, onError: (reason: unknown) => void) {
  const [roots, setRoots] = useState<FileEntry[]>([]);
  const [children, setChildren] = useState<Record<string, FileEntry[]>>({});
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [loading, setLoading] = useState(false);
  const scope = useRef('');
  const report = useRef(onError);
  report.current = onError;
  const target = useMemo(() => ({project, session}), [project?.id, project?.path, session?.id, session?.cwd, session?.worktree_id, session?.runner_id]);
  const key = JSON.stringify([project?.id, session?.cwd || project?.path, session?.id, session?.worktree_id]);

  const load = useCallback(
    async (path: string) => {
      const mine = scope.current;
      try {
        const rows = await listTree(target, path);
        if (scope.current !== mine) return;
        if (path) setChildren(old => ({...old, [path]: rows}));
        else setRoots(rows);
      } catch (reason) {
        if (scope.current === mine) report.current(reason);
      }
    },
    [target],
  );

  useEffect(() => {
    scope.current = key;
    setRoots([]);
    setChildren({});
    setExpanded(new Set());
    if (!project) return;
    setLoading(true);
    void load('').finally(() => {
      if (scope.current === key) setLoading(false);
    });
  }, [key, load]);

  const toggle = useCallback(
    (path: string) => {
      const opening = !expanded.has(path);
      setExpanded(old => {
        const next = new Set(old);
        if (opening) next.add(path);
        else next.delete(path);
        return next;
      });
      if (opening && !children[path]) void load(path);
    },
    [expanded, children, load],
  );

  const refresh = useCallback(() => {
    void load('');
    for (const path of expanded) void load(path);
  }, [expanded, load]);

  const rows = useMemo(() => {
    const out: TreeRow[] = [];
    const walk = (entries: FileEntry[], depth: number) => {
      for (const entry of [...entries].sort(byKind)) {
        const directory = isDirectory(entry);
        const open = directory && expanded.has(entry.path);
        out.push({entry, depth, directory, expanded: open, name: entry.name || entry.path.split('/').at(-1) || entry.path});
        if (open) walk(children[entry.path] ?? [], depth + 1);
      }
    };
    walk(roots, 0);
    return out;
  }, [roots, children, expanded]);

  return {rows, loading, toggle, refresh};
}
