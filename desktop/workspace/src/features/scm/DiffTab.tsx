import {useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {Button} from '../../components/ui/button';
import {request, type Project, type Session} from '../../lib/api';
import {sessionChangesPath, type SessionChanges} from '../../lib/session-changes';
import DiffBody, {type DiffMode} from './DiffBody';
import DiffToolbar from './DiffToolbar';
import {buildBlocks, EXPAND_STEP} from './diff-blocks';
import {diffRef, parseDiffRef} from './diff-ref';
import {hunkPatch, totals, type Hunk} from './patch';
import {runOperation} from './scm-api';
import {DIFF_STEP_EVENT, notifyScmChanged, type DiffStepDetail} from './scm-bus';
import {useDiff} from './use-diff';
import './scm.css';
import './diff.css';

const MODE_KEY = 'termx-diff-mode';
/** More rows than this are drawn a step at a time, so a huge diff never builds a huge page. */
const ROW_BUDGET = 4000;
const NARROW = 560;

function storedMode(): DiffMode {
  try {
    return localStorage.getItem(MODE_KEY) === 'split' ? 'split' : 'inline';
  } catch {
    return 'inline';
  }
}

type Props = {
  /** `staged:path` or `worktree:path`. A tab opened without one picks the first changed file. */
  tabRef?: string;
  visible: boolean;
  project: Project | null;
  session: Session | null;
  onOpenFile: (path: string) => void;
  onRetarget: (ref: string) => void;
  onClose: () => void;
};

function Note({text, children}: {text: string; children?: ReactNode}) {
  return (
    <div className="diff-note" role="status">
      <p>{text}</p>
      {children}
    </div>
  );
}

/** A diff tab opened with no file: it shows the first changed file, or says the checkout is clean. */
function FirstChange({project, session, visible, onRetarget}: Pick<Props, 'project' | 'session' | 'visible' | 'onRetarget'>) {
  const [clean, setClean] = useState(false);
  useEffect(() => {
    if (!project || !session || !visible) return;
    let stopped = false;
    request<SessionChanges>(sessionChangesPath(session))
      .then(changes => {
        if (stopped) return;
        const file = changes.files.find(item => item.reviewable);
        if (!file) setClean(true);
        else onRetarget(diffRef(file.untracked || file.worktree.trim() ? 'worktree' : 'staged', file.path));
      })
      .catch(() => setClean(true));
    return () => {
      stopped = true;
    };
  }, [project?.id, session?.id, session?.worktree_id, visible]);
  if (!project || !session) return <Note text="Open a project conversation to review its changes." />;
  return <Note text={clean ? 'No changes. The working tree matches the last commit.' : 'Finding the first changed file…'} />;
}

/**
 * One file's diff in a tab, inline or side by side. The patch comes from the host (it returns patches, not file contents), so
 * a hunk is staged or unstaged by sending that hunk back as a patch. F7 and Shift+F7 move between changes.
 */
export default function DiffTab(props: Props) {
  const parsed = parseDiffRef(props.tabRef);
  if (!parsed) return <FirstChange {...props} />;
  return <FileDiff key={props.tabRef} {...props} side={parsed.side} path={parsed.path} />;
}

function FileDiff({project, session, visible, side, path, onOpenFile, onClose}: Props & {side: 'staged' | 'worktree'; path: string}) {
  const {data, reload} = useDiff({project, session, side, path, visible});
  const worktreeId = session?.worktree_id || '';
  const [mode, setMode] = useState<DiffMode>(storedMode);
  const [narrow, setNarrow] = useState(false);
  const [revealed, setRevealed] = useState<Record<number, number>>({});
  const [rows, setRows] = useState(ROW_BUDGET);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const current = useRef(-1);
  const [focusAt, setFocusAt] = useState<{index: number; n: number} | null>(null);

  useEffect(() => {
    const node = root.current;
    if (!node || typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < NARROW));
    watch.observe(node);
    return () => watch.disconnect();
  }, []);

  const ready = data.status === 'ready' ? data : null;
  const showMode: DiffMode = narrow ? 'inline' : mode;
  const blocks = useMemo(() => (ready ? buildBlocks(ready.file, ready.lines, revealed) : []), [ready?.file, ready?.lines, revealed]);
  const hunkCount = ready?.file.hunks.length ?? 0;
  const shown = useMemo(() => {
    let used = 0;
    const out = [];
    for (const block of blocks) {
      const size = block.type === 'hunk' ? block.hunk.lines.length : block.rows.length;
      if (used >= rows && block.type === 'hunk') break;
      used += size;
      out.push(block);
    }
    return out;
  }, [blocks, rows]);
  const hiddenHunks = hunkCount - shown.filter(block => block.type === 'hunk').length;

  function step(direction: 'next' | 'previous') {
    if (!hunkCount) return;
    let index = current.current + (direction === 'next' ? 1 : -1);
    if (index >= hunkCount) index = 0;
    if (index < 0) index = hunkCount - 1;
    current.current = index;
    if (hiddenHunks > 0) setRows(Infinity);
    setFocusAt(previous => ({index, n: (previous?.n ?? 0) + 1}));
  }
  useEffect(() => {
    if (!focusAt) return;
    const node = scroll.current?.querySelector<HTMLElement>('[data-hunk="' + focusAt.index + '"]');
    node?.focus();
    node?.scrollIntoView?.({block: 'nearest'});
  }, [focusAt]);

  const stepper = useRef(step);
  stepper.current = step;
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<DiffStepDetail>).detail;
      const node = root.current;
      if (!visible || !node || detail.claimed) return;
      const inside = node.contains(document.activeElement);
      if (!inside && document.activeElement?.closest('.diff-tab')) return;
      detail.claimed = true;
      stepper.current(detail.step);
    };
    window.addEventListener(DIFF_STEP_EVENT, listener);
    return () => window.removeEventListener(DIFF_STEP_EVENT, listener);
  }, [visible]);

  async function mutate(operation: string, args: Record<string, unknown>) {
    if (!project || busy) return;
    setBusy(true);
    setError('');
    try {
      const {action} = await runOperation(project, worktreeId, operation, args);
      if (action) throw new Error('This operation needs a confirmation. Use Source control.');
      notifyScmChanged();
    } catch (reason) {
      setError((operation === 'hunk' ? 'Hunk change' : 'Change') + ' failed: ' + (reason instanceof Error ? reason.message : String(reason)) + '.');
    } finally {
      setBusy(false);
    }
  }

  const changeMode = (next: DiffMode) => {
    setMode(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      // Stored for this device when storage allows it.
    }
  };
  const stageSide = side === 'worktree';
  const hunkAction =
    ready && !ready.untracked
      ? {label: stageSide ? 'Stage hunk' : 'Unstage hunk', busy, run: (hunk: Hunk) => void mutate('hunk', {patch: hunkPatch(ready.file, hunk), stage: stageSide})}
      : null;
  const counts = ready && !ready.binary && !ready.tooLarge ? totals(ready.file) : null;
  const empty = ready && !ready.binary && !ready.tooLarge && ready.file.hunks.length === 0;

  const body = (() => {
    if (!project || !session) return <Note text="Open a project conversation to review its changes." />;
    if (data.status === 'loading') return <Note text="Loading diff…" />;
    if (data.status === 'error') {
      return (
        <div className="diff-note" role="alert">
          <p>Could not read this diff: {data.message}.</p>
          <Button variant="outline" size="sm" onClick={() => void reload()}>
            Try again
          </Button>
        </div>
      );
    }
    if (data.binary) return <Note text="Binary file changed. A text preview is not available." />;
    if (data.tooLarge) {
      return (
        <Note text="This diff is too large to show here.">
          <Button variant="outline" size="sm" onClick={() => onOpenFile(path)}>
            Open file in editor
          </Button>
        </Note>
      );
    }
    if (empty) {
      return (
        <Note text={side === 'staged' ? 'Nothing is staged for this file.' : 'No unstaged changes in this file.'}>
          <Button variant="outline" size="sm" onClick={onClose}>
            Close tab
          </Button>
        </Note>
      );
    }
    return (
      <>
        {showMode === 'split' && (
          <div className="diff-split-labels" aria-hidden="true">
            <span>Before</span>
            <span>After</span>
          </div>
        )}
        <DiffBody
          blocks={shown}
          mode={showMode}
          total={hunkCount}
          action={hunkAction}
          onExpand={(gap, hidden) => setRevealed(value => ({...value, [gap]: (value[gap] ?? 0) + Math.min(EXPAND_STEP, hidden)}))}
        />
      </>
    );
  })();

  return (
    <div className="diff-tab" ref={root}>
      <DiffToolbar
        path={path}
        side={side}
        counts={counts}
        mode={mode}
        narrow={narrow}
        canStep={hunkCount > 1}
        busy={busy}
        fileAction={
          ready && !ready.binary
            ? {label: stageSide ? 'Stage file' : 'Unstage file', run: () => void mutate(stageSide ? 'stage' : 'unstage', {paths: [path]})}
            : null
        }
        onOpenFile={ready && !ready.binary && !(ready.file.hunks.length === 0) ? () => onOpenFile(path) : null}
        onMode={changeMode}
        onStep={step}
        onRefresh={() => void reload()}
      />
      {error && (
        <p className="diff-error" role="alert">
          {error}
        </p>
      )}
      <div
        className="diff-scroll"
        ref={scroll}
        role="region"
        tabIndex={0}
        aria-label={(showMode === 'split' ? 'Side by side' : 'Inline') + ' diff for ' + path}
        data-mode={showMode}
        onFocusCapture={event => {
          const index = (event.target as HTMLElement).dataset.hunk;
          if (index !== undefined) current.current = Number(index);
        }}
        onKeyDown={event => {
          if (event.key !== 'F7' || event.metaKey || event.ctrlKey || event.altKey) return;
          event.preventDefault();
          step(event.shiftKey ? 'previous' : 'next');
        }}
      >
        {body}
        {hiddenHunks > 0 && (
          <button type="button" className="diff-expand" onClick={() => setRows(Infinity)}>
            Show {hiddenHunks} more {hiddenHunks === 1 ? 'change' : 'changes'}
          </button>
        )}
      </div>
    </div>
  );
}
