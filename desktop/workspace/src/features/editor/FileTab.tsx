import {Transaction} from '@codemirror/state';
import {EditorView} from '@codemirror/view';
import {Ellipsis, Save, X} from 'lucide-react';
import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import type {Project} from '../../lib/api';
import {useReport} from '../../shell/errors';
import {useWorkspaceData} from '../../shell/WorkspaceDataProvider';
import Breadcrumbs from './Breadcrumbs';
import {useBuffers} from './buffer-store';
import {bufferKey, executionRoot, readFile} from './file-io';
import {FILE_LINE_EVENT, takeFileLine} from './file-navigation';
import {applyLanguage, createEditorState} from './extensions';
import {resolveLanguage} from './language';
import {useLanguageServer} from './use-language-server';
import './editor.css';
import './file-tab.css';

type Props = {
  /** The buffer key (`project:folder:path`); `undefined` for an editor tab that has no file yet. */
  fileKey: string | undefined;
  visible: boolean;
  project: Project | null;
  /** Show Quick open: the one way to choose a file when this tab has none. */
  onFindFile: () => void;
  /** Open another file at a line, in a tab of its own. */
  onOpenFile: (path: string, line: number) => void;
  /** Close the tab (a file that cannot be shown here). */
  onClose: () => void;
};

/**
 * One file in one CodeMirror editor: the content of a `file` tab.
 *
 * The buffer lives in the window's buffer store, so this tab can move between groups, split or be hidden without losing
 * edits; only the editor view lives here, and the layout's keep-alive layer keeps it mounted across moves. A tab restored from
 * a stored layout has no buffer yet: it reads the file again when it belongs to this conversation's checkout.
 */
export default function FileTab({fileKey, visible, project, onFindFile, onOpenFile, onClose}: Props) {
  const store = useBuffers();
  const report = useReport();
  const {session} = useWorkspaceData();
  const buffer = fileKey ? store.buffers.find(row => row.key === fileKey) : undefined;
  const [problem, setProblem] = useState('');
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const synchronizing = useRef(false);
  const language = useRef('');
  const latest = useRef({store, key: fileKey, visible});
  latest.current = {store, key: fileKey, visible};
  const target = {project, session};

  // A tab restored from a stored layout reads its file again, if this conversation's checkout is the one it was opened in.
  useEffect(() => {
    if (!fileKey || buffer || !store.ready || !project) return;
    const prefix = bufferKey(target, '');
    if (!fileKey.startsWith(prefix)) {
      setProblem('This file was opened in another conversation or checkout. Open that conversation to see it.');
      return;
    }
    const path = fileKey.slice(prefix.length);
    let stopped = false;
    setProblem('');
    readFile(target, path)
      .then(data => {
        if (stopped) return;
        store.add({
          key: fileKey,
          project_id: project.id,
          path: data.path || path,
          content: data.content,
          revision: data.revision,
          editable: data.editable,
          dirty: false,
          root: executionRoot(target),
          workspaceSession: session && !session.runner_id ? session.id : undefined,
          worktree: session?.worktree_id,
        });
      })
      .catch(reason => {
        if (!stopped) setProblem(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      stopped = true;
    };
  }, [fileKey, !!buffer, store.ready, project?.id, session?.id, session?.worktree_id]);

  const path = buffer?.path ?? '';
  const present = !!buffer;

  // Create the editor once the buffer exists; it lives as long as the tab.
  useEffect(() => {
    if (!present || !host.current || !fileKey) return;
    const current = latest.current.store.get(fileKey)!;
    const reportCursor = (instance: EditorView) => {
      const head = instance.state.selection.main.head;
      const line = instance.state.doc.lineAt(head);
      latest.current.store.setCursor(fileKey, {line: line.number, column: head - line.from + 1, language: language.current});
    };
    const instance = new EditorView({
      parent: host.current,
      state:
        current.state ??
        createEditorState({
          doc: current.content,
          label: 'Editor for ' + current.path,
          path: current.path,
          editable: current.editable,
          onSave: () => void latest.current.store.save(fileKey),
          onDocChange: next => {
            if (synchronizing.current) return;
            latest.current.store.patch(fileKey, {content: next.doc.toString(), dirty: true, state: next});
          },
          extra: [
            EditorView.updateListener.of(update => {
              if (update.selectionSet || update.docChanged) reportCursor(update.view);
            }),
          ],
        }),
    });
    instance.scrollDOM.tabIndex = 0;
    instance.scrollDOM.setAttribute('aria-label', 'Editor viewport for ' + current.path);
    view.current = instance;
    void resolveLanguage(current.path).then(found => {
      language.current = found?.name ?? '';
      if (view.current === instance) reportCursor(instance);
    });
    void applyLanguage(instance, current.path, () => view.current === instance);
    reportCursor(instance);
    return () => {
      latest.current.store.setCursor(fileKey, null);
      instance.destroy();
      view.current = null;
    };
  }, [present, fileKey]);

  // The buffer changed under the editor (another window, a handoff): show it, without counting it as an edit.
  useEffect(() => {
    const instance = view.current;
    if (!instance || !buffer || instance.state.doc.toString() === buffer.content) return;
    synchronizing.current = true;
    try {
      instance.dispatch({
        changes: {from: 0, to: instance.state.doc.length, insert: buffer.content},
        annotations: Transaction.addToHistory.of(false),
      });
    } finally {
      synchronizing.current = false;
    }
  }, [buffer?.content]);

  // Open at a requested line, now or when the request arrives.
  const reveal = useCallback(() => {
    const instance = view.current;
    const key = latest.current.key;
    if (!instance || !key || !latest.current.visible) return;
    const line = takeFileLine(key);
    if (line === undefined) return;
    const row = instance.state.doc.line(Math.max(1, Math.min(instance.state.doc.lines, line)));
    instance.dispatch({selection: {anchor: row.from}, scrollIntoView: true});
    instance.focus();
  }, []);
  useEffect(() => {
    const frame = requestAnimationFrame(reveal);
    window.addEventListener(FILE_LINE_EVENT, reveal);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener(FILE_LINE_EVENT, reveal);
    };
  }, [reveal, visible, present]);

  const lsp = useLanguageServer({project, session, buffer, view: () => view.current, onError: report, onOpen: onOpenFile});

  if (!fileKey) {
    return (
      <div className="tab-empty" role="status">
        <p>Open a file to start editing.</p>
        <Button variant="outline" onClick={onFindFile}>
          Find a file
        </Button>
      </div>
    );
  }
  if (!buffer) {
    return problem ? (
      <div className="tab-empty" role="alert">
        <p>{problem}</p>
        <Button variant="outline" onClick={onClose}>
          Close tab
        </Button>
      </div>
    ) : (
      <p className="panel-loading" role="status">
        Opening file…
      </p>
    );
  }
  const saving = store.saving.has(fileKey);
  return (
    <section className="file-tab" aria-label={'File ' + path}>
      <div className="file-tab-bar">
        <Breadcrumbs path={path} readOnly={!buffer.editable} />
        <div className="file-tab-actions">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Save file"
            title="Save file"
            disabled={!buffer.dirty || saving}
            onClick={() => void store.save(fileKey)}
          >
            <Save size={16} strokeWidth={1.5} aria-hidden="true" />
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Editor actions" title="Editor actions">
                <Ellipsis size={16} strokeWidth={1.5} aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem disabled={!lsp.supported} onSelect={() => void lsp.run('definition')}>
                Go to definition
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!lsp.supported} onSelect={() => void lsp.run('hover')}>
                Show documentation
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!lsp.supported} onSelect={() => void lsp.run('completion')}>
                Show completions
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!lsp.supported || !buffer.editable} onSelect={() => void lsp.format()}>
                Format document
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <div className="file-tab-host" ref={host} />
      {lsp.output && (
        <div className="file-tab-output">
          <Button variant="ghost" size="icon" aria-label="Close language results" onClick={lsp.clearOutput}>
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </Button>
          <pre>{lsp.output}</pre>
        </div>
      )}
    </section>
  );
}

