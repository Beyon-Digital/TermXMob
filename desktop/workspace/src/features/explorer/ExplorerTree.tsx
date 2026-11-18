import {ChevronDown, ChevronRight, File, Folder} from 'lucide-react';
import {useEffect, useRef, useState, type KeyboardEvent} from 'react';
import type {TreeRow} from './use-file-tree';
import './explorer.css';

type Props = {
  rows: readonly TreeRow[];
  /** The path of the file the focused editor shows, marked as selected. */
  activePath?: string;
  /** Source control letters by path (`M`, `A`, `D`, `U`): a letter, never colour alone. */
  decorations?: Readonly<Record<string, string>>;
  disabled?: boolean;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
};

const LABEL: Record<string, string> = {M: 'modified', A: 'added', D: 'deleted', U: 'untracked', R: 'renamed'};

/**
 * The file tree as an ARIA tree: one Tab stop, arrows move through the rows, Right and Left open, close and step in or out of
 * a folder, Enter opens a file or toggles a folder, Home and End jump. The rows are a flat list with levels, which is a
 * valid tree and keeps a 10,000-file folder cheap to render.
 */
export default function ExplorerTree({rows, activePath, decorations = {}, disabled, onToggle, onOpen}: Props) {
  const list = useRef<HTMLDivElement>(null);
  const [stop, setStop] = useState(0);
  const at = Math.min(stop, Math.max(0, rows.length - 1));

  useEffect(() => {
    if (stop > rows.length - 1) setStop(Math.max(0, rows.length - 1));
  }, [rows.length, stop]);

  function focusRow(index: number) {
    const next = Math.max(0, Math.min(rows.length - 1, index));
    setStop(next);
    requestAnimationFrame(() => list.current?.querySelectorAll<HTMLElement>('[role="treeitem"]')[next]?.focus());
  }

  function activate(row: TreeRow) {
    if (disabled) return;
    if (row.directory) onToggle(row.entry.path);
    else onOpen(row.entry.path);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (index < 0) return;
    const row = rows[index];
    switch (event.key) {
      case 'ArrowDown':
        focusRow(index + 1);
        break;
      case 'ArrowUp':
        focusRow(index - 1);
        break;
      case 'Home':
        focusRow(0);
        break;
      case 'End':
        focusRow(rows.length - 1);
        break;
      case 'ArrowRight':
        if (row.directory && !row.expanded) onToggle(row.entry.path);
        else if (row.directory && rows[index + 1]?.depth > row.depth) focusRow(index + 1);
        break;
      case 'ArrowLeft':
        if (row.directory && row.expanded) onToggle(row.entry.path);
        else {
          let parent = index - 1;
          while (parent >= 0 && rows[parent].depth >= row.depth) parent--;
          if (parent >= 0) focusRow(parent);
        }
        break;
      case 'Enter':
      case ' ':
        activate(row);
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  return (
    <div ref={list} className="explorer-tree" role="tree" aria-label="Files" onKeyDown={onKeyDown}>
      {rows.map((row, index) => {
        const letter = decorations[row.entry.path];
        const selected = !row.directory && row.entry.path === activePath;
        return (
          <div
            key={row.entry.path}
            role="treeitem"
            className="tree-row"
            tabIndex={index === at ? 0 : -1}
            aria-level={row.depth + 1}
            aria-expanded={row.directory ? row.expanded : undefined}
            aria-selected={selected}
            aria-disabled={disabled || undefined}
            data-path={row.entry.path}
            title={row.entry.path}
            style={{paddingLeft: 8 + row.depth * 16}}
            onFocus={() => setStop(index)}
            onClick={() => activate(row)}
          >
            {row.directory ? (
              row.expanded ? <ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" /> : <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" />
            ) : (
              <span className="tree-indent" aria-hidden="true" />
            )}
            {row.directory ? <Folder size={16} strokeWidth={1.5} aria-hidden="true" /> : <File size={16} strokeWidth={1.5} aria-hidden="true" />}
            <span className="tree-name">{row.name}</span>
            {letter && (
              <span className="tree-status" title={LABEL[letter] ?? letter}>
                <span aria-hidden="true">{letter}</span>
                <span className="visually-hidden">, {LABEL[letter] ?? letter}</span>
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
