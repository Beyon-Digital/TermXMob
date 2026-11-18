import {useEffect, useId, useMemo, useState} from 'react';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {quickOpen, type FileTarget} from '../../features/editor/file-io';
import {formatShortcut} from '../../lib/shortcut-label';
import {paletteEntries, type Command, type CommandContext, type PaletteEntry} from '../commands';
import './quick-open.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Start with commands (a `>` prefix) instead of files. */
  commandsFirst: boolean;
  target: FileTarget;
  context: CommandContext;
  bindings: Partial<Record<string, string>>;
  onRun: (command: Command) => void;
  /** Open the chosen file in a tab. */
  onOpenFile: (path: string) => void;
};

type Row = {id: string; label: string; hint?: string; group: 'Files' | 'Commands'; run: () => void};

/**
 * Search files and commands: the command center. Plain text finds files in this conversation's checkout; a leading `>` (or
 * Shift+Mod+P) lists commands only. The commands are the registry's, the same list the old palette showed.
 */
function Body({commandsFirst, target, context, bindings, onRun, onOpenFile, close}: Omit<Props, 'open' | 'onOpenChange'> & {close: () => void}) {
  const [query, setQuery] = useState(commandsFirst ? '>' : '');
  const [files, setFiles] = useState<{path: string}[]>([]);
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const onlyCommands = query.startsWith('>');
  const text = onlyCommands ? query.slice(1).trim() : query.trim();

  // File names, shortly after the last keystroke.
  useEffect(() => {
    if (onlyCommands) return;
    let stopped = false;
    setLoading(true);
    const timer = setTimeout(() => {
      quickOpen(target, text)
        .then(found => {
          if (stopped) return;
          setFiles(found.results.slice(0, 20));
          setTruncated(found.truncated || found.results.length > 20);
          setProblem('');
        })
        .catch(reason => {
          if (stopped) return;
          setFiles([]);
          setProblem(reason instanceof Error ? reason.message : String(reason));
        })
        .finally(() => {
          if (!stopped) setLoading(false);
        });
    }, 150);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [onlyCommands, text, target.session?.id, target.session?.worktree_id, target.project?.id]);

  const commands = useMemo(() => {
    const terms = text.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const all: PaletteEntry[] = paletteEntries(context, bindings, command => {
      close();
      onRun(command);
    });
    const matched = all.filter(entry => terms.every(term => (entry.label + ' ' + entry.keywords).toLocaleLowerCase().includes(term)));
    return onlyCommands ? matched : terms.length ? matched.slice(0, 6) : [];
  }, [text, onlyCommands, context, bindings]);

  const rows: Row[] = [
    ...(onlyCommands
      ? []
      : files.map(file => ({
          id: 'file:' + file.path,
          label: file.path,
          group: 'Files' as const,
          run: () => {
            close();
            onOpenFile(file.path);
          },
        }))),
    ...commands.map(entry => ({
      id: 'cmd:' + entry.id,
      label: entry.label,
      hint: entry.shortcut ? formatShortcut(entry.shortcut) : undefined,
      group: 'Commands' as const,
      run: entry.run,
    })),
  ];
  const at = Math.min(active, Math.max(0, rows.length - 1));
  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    document.getElementById(listId + '-' + at)?.scrollIntoView?.({block: 'nearest'});
  }, [at, rows.length]);

  return (
    <>
      <Input
        autoFocus
        role="combobox"
        aria-autocomplete="list"
        aria-expanded
        aria-controls={listId}
        aria-activedescendant={rows[at] ? listId + '-' + at : undefined}
        aria-label="Search files and commands"
        placeholder="Search files by name, or type > for commands"
        value={query}
        maxLength={200}
        // Opening in commands mode starts with ">" already typed: the caret goes after it, so typing never replaces it.
        onFocus={event => event.currentTarget.setSelectionRange(event.currentTarget.value.length, event.currentTarget.value.length)}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActive(Math.min(rows.length - 1, at + 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive(Math.max(0, at - 1));
          } else if (event.key === 'Enter' && rows[at]) {
            event.preventDefault();
            rows[at].run();
          }
        }}
      />
      <div id={listId} className="quick-open-list" role="listbox" aria-label="Results">
        {rows.map((row, index) => (
          <div
            key={row.id}
            id={listId + '-' + index}
            role="option"
            aria-selected={index === at}
            className="quick-open-row"
            data-group={row.group}
            onMouseMove={() => setActive(index)}
            onClick={row.run}
          >
            <span className="quick-open-label">{row.label}</span>
            <span className="quick-open-group">{row.hint ?? row.group}</span>
          </div>
        ))}
      </div>
      <p className="quick-open-note" role="status">
        {loading && !onlyCommands
          ? 'Finding files…'
          : problem && !commands.length
            ? problem
            : !rows.length
              ? onlyCommands
                ? 'No matching commands.'
                : 'No matching files or commands.'
              : truncated
                ? 'Narrow the file name to see more results.'
                : ''}
      </p>
    </>
  );
}

export default function QuickOpenDialog(props: Props) {
  const {open, onOpenChange, ...body} = props;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="quick-open">
        <DialogTitle>Search files and commands</DialogTitle>
        <DialogDescription>Find a file in this checkout, or type &gt; to run a command.</DialogDescription>
        {open && <Body {...body} close={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
