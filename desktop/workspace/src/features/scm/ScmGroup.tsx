import {ChevronRight} from 'lucide-react';
import {useId, useState, type KeyboardEvent} from 'react';
import ScmRow from './ScmRow';
import type {ScmEntry} from './scm-types';

type Props = {
  title: string;
  entries: ScmEntry[];
  busy: boolean;
  /** The group's bulk action ("Stage all", "Unstage all"), when it has one. */
  bulk?: {label: string; aria: string; run: () => void};
  /** A second group action ("Discard all"), drawn after the first. */
  secondary?: {label: string; aria: string; run: () => void};
  onOpen: (entry: ScmEntry) => void;
  onOpenFile: (path: string) => void;
  onStage: (entry: ScmEntry) => void;
  onUnstage: (entry: ScmEntry) => void;
  onDiscard: (entry: ScmEntry) => void;
};

/** Up, Down, Home and End move between the rows of a group; Tab leaves it. */
function moveFocus(event: KeyboardEvent<HTMLUListElement>) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  const target = event.target as HTMLElement;
  if (!target.matches('[data-scm-file]')) return;
  const rows = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[data-scm-file]:not(:disabled)'));
  const at = rows.indexOf(target as HTMLButtonElement);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : Math.min(rows.length - 1, Math.max(0, at + (event.key === 'ArrowDown' ? 1 : -1)));
  event.preventDefault();
  rows[next]?.focus();
}

/** A collapsible group of changed files with its count and bulk action. */
export default function ScmGroup({title, entries, busy, bulk, secondary, onOpen, onOpenFile, onStage, onUnstage, onDiscard}: Props) {
  const [open, setOpen] = useState(true);
  const id = useId();
  return (
    <section className="scm-group" aria-labelledby={id}>
      <div className="scm-group-head">
        <h3 className="scm-group-title" id={id}>
          <button type="button" className="scm-group-toggle" aria-expanded={open} onClick={() => setOpen(value => !value)}>
            <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" className="scm-chevron" />
            <span>{title}</span>
            <span className="scm-count">{entries.length}</span>
          </button>
        </h3>
        {[bulk, secondary].map(
          action =>
            action && (
              <button key={action.label} type="button" className="scm-text-action" aria-label={action.aria} disabled={busy} onClick={action.run}>
                {action.label}
              </button>
            ),
        )}
      </div>
      {open && (
        <ul className="view-list scm-list" aria-label={title} onKeyDown={moveFocus}>
          {entries.map(entry => (
            <ScmRow key={entry.key} entry={entry} busy={busy} onOpen={onOpen} onOpenFile={onOpenFile} onStage={onStage} onUnstage={onUnstage} onDiscard={onDiscard} />
          ))}
        </ul>
      )}
    </section>
  );
}
