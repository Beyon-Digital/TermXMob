import type {EditorState} from '@codemirror/state';
import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import type {Session} from '../../lib/api';
import type {Diagnostic} from '../../lib/lsp';
import {
  mergeBuffers,
  persistBuffers,
  publishBuffers,
  restoreBuffers,
  snapshotBuffers,
  type TransferBuffer,
} from '../../lib/window-state';
import {writeFile} from './file-io';

/**
 * The open files of the new layout, above the tabs.
 *
 * Each file tab shows one buffer, but the buffers belong to the window, not to the tab: a tab that moves to another group
 * keeps its content and dirty state, and a window handoff (detach, redock, crash recovery) carries every buffer through the
 * same functions the old editor used (`lib/window-state.ts`; the payload shape is unchanged). Clean buffers are published
 * for the handoff snapshot; only dirty ones are persisted for recovery.
 */

export type EditorBuffer = TransferBuffer & {state?: EditorState};

/** Where the focused editor's caret is, for the status bar. */
export type EditorCursor = {line: number; column: number; language: string};

export type BufferStore = {
  buffers: readonly EditorBuffer[];
  /** The stored buffers of this window have been restored. */
  ready: boolean;
  get: (key: string) => EditorBuffer | undefined;
  /** Add a buffer, or keep the one that is already open under this key. */
  add: (buffer: EditorBuffer) => void;
  patch: (key: string, change: Partial<EditorBuffer> | ((buffer: EditorBuffer) => Partial<EditorBuffer>)) => void;
  remove: (key: string) => void;
  save: (key: string) => Promise<boolean>;
  saving: ReadonlySet<string>;
  /** The diagnostics each language server reported, by file URI. */
  diagnostics: Readonly<Record<string, Diagnostic[]>>;
  setDiagnostics: (uri: string, items: Diagnostic[]) => void;
  /** The caret of each open editor, by buffer key. The status bar shows the one in the focused tab. */
  cursors: Readonly<Record<string, EditorCursor>>;
  setCursor: (key: string, cursor: EditorCursor | null) => void;
};

const Context = createContext<BufferStore | null>(null);

export function useBuffers(): BufferStore {
  const store = useContext(Context);
  if (!store) throw new Error('useBuffers needs a BufferProvider');
  return store;
}

/** Like `useBuffers`, but `null` outside a provider (the old layout has its own buffers inside the Workbench). */
export function useOptionalBuffers(): BufferStore | null {
  return useContext(Context);
}

type Props = {
  ownerId: string;
  /** The selected conversation: a buffer can be saved only from the conversation and checkout it was opened in. */
  session: Session | null;
  onError: (reason: unknown) => void;
  children: ReactNode;
};

export function BufferProvider({ownerId, session, onError, children}: Props) {
  const [buffers, setBuffers] = useState<EditorBuffer[]>([]);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState<ReadonlySet<string>>(() => new Set());
  const [diagnostics, setDiagnosticsState] = useState<Record<string, Diagnostic[]>>({});
  const [cursors, setCursors] = useState<Record<string, EditorCursor>>({});
  const latest = useRef(buffers);
  latest.current = buffers;
  const sessionNow = useRef(session);
  sessionNow.current = session;
  const report = useRef(onError);
  report.current = onError;
  const channel = useRef<BroadcastChannel | null>(null);

  // Restore what this window left behind, and take buffers a detached window hands over.
  useEffect(() => {
    let stopped = false;
    const incoming = (event: Event) => {
      const detail = (event as CustomEvent<{owner: string; buffers: TransferBuffer[]}>).detail;
      if (detail.owner === ownerId) setBuffers(rows => mergeBuffers(rows, detail.buffers, 'live') as EditorBuffer[]);
    };
    window.addEventListener('termx-buffer-handoff', incoming);
    restoreBuffers(ownerId)
      .then(rows => {
        if (stopped) return;
        const merged = mergeBuffers(rows, snapshotBuffers(ownerId), 'restored');
        setBuffers(current => mergeBuffers(current, merged, 'startup') as EditorBuffer[]);
        setReady(true);
      })
      .catch(reason => {
        if (stopped) return;
        setReady(true);
        report.current(reason);
      });
    return () => {
      stopped = true;
      window.removeEventListener('termx-buffer-handoff', incoming);
    };
  }, [ownerId]);

  // Publish every change for a handoff snapshot; persist the dirty ones shortly after for crash recovery.
  useEffect(() => {
    if (!ready) return;
    publishBuffers(ownerId, buffers);
    const timer = setTimeout(() => persistBuffers(ownerId, buffers).catch(reason => report.current(reason)), 200);
    return () => clearTimeout(timer);
  }, [ownerId, buffers, ready]);

  // Another window saved the same file; leaving with unsaved edits asks first (a redock in progress is not leaving).
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const bus = new BroadcastChannel('termx-editor-revisions');
    channel.current = bus;
    bus.onmessage = event => {
      const message = event.data;
      const buffer = latest.current.find(row => row.project_id === message.project_id && row.path === message.path && row.root === message.root);
      if (buffer && buffer.revision !== message.revision) {
        report.current(new Error(buffer.path + ' changed in another window. Review your local edits before saving.'));
      }
    };
    let committing = false;
    const commit = (event: Event) => {
      committing = !!(event as CustomEvent).detail;
    };
    const leave = (event: BeforeUnloadEvent) => {
      if (!committing && latest.current.some(row => row.dirty)) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('termx-redock-committing', commit);
    window.addEventListener('beforeunload', leave);
    return () => {
      bus.close();
      channel.current = null;
      window.removeEventListener('termx-redock-committing', commit);
      window.removeEventListener('beforeunload', leave);
    };
  }, []);

  const get = useCallback((key: string) => latest.current.find(row => row.key === key), []);
  const add = useCallback((buffer: EditorBuffer) => {
    setBuffers(rows => (rows.some(row => row.key === buffer.key) ? rows : [...rows, buffer]));
  }, []);
  const patch = useCallback<BufferStore['patch']>((key, change) => {
    setBuffers(rows => rows.map(row => (row.key === key ? {...row, ...(typeof change === 'function' ? change(row) : change)} : row)));
  }, []);
  const remove = useCallback((key: string) => setBuffers(rows => rows.filter(row => row.key !== key)), []);

  const save = useCallback(
    async (key: string) => {
      const buffer = latest.current.find(row => row.key === key);
      if (!buffer || !buffer.editable || !buffer.dirty) return false;
      setSaving(rows => new Set(rows).add(key));
      try {
        const now = sessionNow.current;
        if (buffer.workspaceSession && (!now || now.id !== buffer.workspaceSession || now.worktree_id !== buffer.worktree)) {
          throw new Error('Return to this buffer’s original conversation and checkout before saving. Its unsaved edits are preserved.');
        }
        const revision = await writeFile(buffer);
        setBuffers(rows => rows.map(row => (row.key === key ? {...row, revision, dirty: row.content !== buffer.content} : row)));
        channel.current?.postMessage({project_id: buffer.project_id, path: buffer.path, root: buffer.root, revision});
        return true;
      } catch (reason) {
        report.current(reason);
        return false;
      } finally {
        setSaving(rows => {
          const next = new Set(rows);
          next.delete(key);
          return next;
        });
      }
    },
    [],
  );

  const setDiagnostics = useCallback((uri: string, items: Diagnostic[]) => setDiagnosticsState(old => ({...old, [uri]: items})), []);

  const setCursor = useCallback((key: string, value: EditorCursor | null) => {
    setCursors(old => {
      const same = old[key] && value && old[key].line === value.line && old[key].column === value.column && old[key].language === value.language;
      if (same || (!value && !old[key])) return old;
      const next = {...old};
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
  }, []);

  const store = useMemo<BufferStore>(
    () => ({buffers, ready, get, add, patch, remove, save, saving, diagnostics, setDiagnostics, cursors, setCursor}),
    [buffers, ready, get, add, patch, remove, save, saving, diagnostics, setDiagnostics, cursors, setCursor],
  );
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
