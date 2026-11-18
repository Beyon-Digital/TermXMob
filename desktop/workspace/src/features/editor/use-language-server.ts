import {useCallback, useEffect, useRef, useState} from 'react';
import type {Project, Session} from '../../lib/api';
import {LanguageClient, fileUri, positionAt, relativeFileUri, type Location} from '../../lib/lsp';
import type {EditorView} from '@codemirror/view';
import {useBuffers, type EditorBuffer} from './buffer-store';

/** The grammar server a path needs: Python or JavaScript and TypeScript. Anything else has none. */
export const languageFor = (path: string) => (path.endsWith('.py') ? 'python' : /\.[cm]?[jt]sx?$/.test(path) ? 'typescript' : null);

type Options = {
  project: Project | null;
  session: Session | null;
  buffer: EditorBuffer | undefined;
  view: () => EditorView | null;
  onError: (reason: unknown) => void;
  /** Open another file at a line (Go to definition). */
  onOpen: (path: string, line: number) => void;
};

/**
 * Language navigation for one file tab: diagnostics flow into the shared store (the Problems tab reads them), and the editor
 * actions (definition, documentation, completions, format) are the ones the old editor offered. One server connection is
 * kept per project, checkout and language for the life of the tab.
 */
export function useLanguageServer({project, session, buffer, view, onError, onOpen}: Options) {
  const {setDiagnostics} = useBuffers();
  const clients = useRef(new Map<string, LanguageClient>());
  const opened = useRef(false);
  const version = useRef(1);
  const [output, setOutput] = useState('');
  const executionRoot = session?.cwd || project?.path || '';

  const clientFor = useCallback(
    (target: EditorBuffer) => {
      const language = languageFor(target.path);
      if (!language || !project || project.id !== target.project_id) return null;
      if (target.workspaceSession && target.worktree !== session?.worktree_id) return null;
      const root = target.root || executionRoot;
      const key = project.id + ':' + root + ':' + language;
      if (!clients.current.has(key)) {
        clients.current.set(key, new LanguageClient(project.id, language, root, setDiagnostics, onError, target.workspaceSession));
      }
      return clients.current.get(key)!;
    },
    [project?.id, session?.worktree_id, executionRoot, onError, setDiagnostics],
  );

  useEffect(
    () => () => {
      for (const client of clients.current.values()) client.close();
    },
    [],
  );

  // Tell the server about the file once, then about each edit shortly after it.
  useEffect(() => {
    if (!buffer) return;
    const client = clientFor(buffer);
    if (!client) return;
    const uri = fileUri(buffer.root || executionRoot, buffer.path);
    if (!opened.current) {
      opened.current = true;
      version.current = 1;
      void client.open(uri, languageFor(buffer.path)!, buffer.content).catch(onError);
      return;
    }
    const timer = setTimeout(() => {
      version.current += 1;
      void client.change(uri, buffer.content, version.current).catch(onError);
    }, 200);
    return () => clearTimeout(timer);
  }, [buffer?.content, buffer?.key, clientFor]);

  const run = useCallback(
    async (method: 'definition' | 'hover' | 'completion') => {
      const editor = view();
      if (!buffer || !editor) return;
      try {
        const client = clientFor(buffer);
        if (!client) throw new Error('Choose a Python, JavaScript or TypeScript file for language navigation');
        const root = buffer.root || executionRoot;
        const result = await client.request(method, fileUri(root, buffer.path), positionAt(buffer.content, editor.state.selection.main.head));
        if (method === 'definition') {
          const location = (Array.isArray(result) ? result[0] : result) as Location | null;
          if (!location) {
            setOutput('No definition at this position');
            return;
          }
          const relative = relativeFileUri(root, location.uri);
          if (relative === null) throw new Error('Definition is outside this execution checkout');
          onOpen(relative, location.range.start.line + 1);
        } else if (method === 'hover') {
          const content = result?.contents;
          setOutput(
            typeof content === 'string'
              ? content
              : Array.isArray(content)
                ? content.map((item: string | {value: string}) => (typeof item === 'string' ? item : item.value)).join('\n')
                : content?.value || 'No hover documentation',
          );
        } else {
          const items = Array.isArray(result) ? result : result?.items || [];
          setOutput(
            items
              .slice(0, 80)
              .map((item: {label: string; detail?: string}) => item.label + (item.detail ? ' · ' + item.detail : ''))
              .join('\n') || 'No completions',
          );
        }
      } catch (reason) {
        onError(reason);
      }
    },
    [buffer, clientFor, executionRoot, onError, onOpen, view],
  );

  const format = useCallback(async () => {
    const editor = view();
    if (!buffer || !editor) return;
    const original = buffer.content;
    try {
      const client = clientFor(buffer);
      if (!client) throw new Error('Choose a supported language file to format');
      const edits = (await client.format(fileUri(buffer.root || executionRoot, buffer.path))) as {
        range: {start: {line: number; character: number}; end: {line: number; character: number}};
        newText: string;
      }[];
      if (editor.state.doc.toString() !== original) throw new Error('File changed during formatting. Request formatting again to preserve your edits.');
      const doc = editor.state.doc;
      const offset = (position: {line: number; character: number}) => {
        const line = doc.line(Math.min(doc.lines, position.line + 1));
        return Math.min(line.to, line.from + position.character);
      };
      editor.dispatch({changes: (edits || []).map(edit => ({from: offset(edit.range.start), to: offset(edit.range.end), insert: edit.newText}))});
    } catch (reason) {
      onError(reason);
    }
  }, [buffer, clientFor, executionRoot, onError, view]);

  return {output, clearOutput: () => setOutput(''), run, format, supported: !!buffer && !!languageFor(buffer.path)};
}
