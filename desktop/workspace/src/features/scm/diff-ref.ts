import type {Side} from './scm-types';

/** A diff tab's `ref`: the side, a colon, then the repository-relative path. */
export function diffRef(side: Side, path: string): string {
  return side + ':' + path;
}

export function parseDiffRef(ref: string | undefined): {side: Side; path: string} | null {
  if (!ref) return null;
  const at = ref.indexOf(':');
  const side = ref.slice(0, at);
  const path = ref.slice(at + 1);
  if (at < 0 || !path || (side !== 'staged' && side !== 'worktree')) return null;
  return {side, path};
}

/** The tab title: the file name, and "staged" for the staged side. */
export function diffTitle(ref: string | undefined): string {
  const parsed = parseDiffRef(ref);
  if (!parsed) return 'Changes';
  const name = parsed.path.split('/').filter(Boolean).at(-1) ?? parsed.path;
  return parsed.side === 'staged' ? name + ' (staged)' : name;
}
