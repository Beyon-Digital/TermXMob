import {Ellipsis, Globe, StickyNote} from 'lucide-react';
import type {DragEvent} from 'react';
import {Button} from '../../components/ui/button';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {originLabel, type Site} from '../../lib/sites';
import SiteEditForm from './SiteEditForm';
import SiteNotes from './SiteNotes';

export type SitePanel = 'notes' | 'edit' | 'remove' | null;

export type SiteDrag = {
  over: boolean;
  onDragStart: (event: DragEvent) => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: (event: DragEvent) => void;
  onDragEnd: () => void;
};

type Props = {
  site: Site;
  /** Pinned rows are ordered: they can move, and they are the only ones that can be dragged. */
  pinned: boolean;
  index: number;
  count: number;
  panel: SitePanel;
  drag?: SiteDrag;
  onPanel: (panel: SitePanel) => void;
  onOpen: () => void;
  onMove: (delta: -1 | 1) => void;
  onPin: () => void;
  onUnpin: () => void;
  onRemove: () => void;
  onEdit: (patch: Partial<Pick<Site, 'title' | 'open_mode'>>) => void | Promise<unknown>;
  onNotesChanged: () => void;
};

/** One saved site: a 36px row that opens it, a notes toggle and an actions menu. Details open inline below the row. */
export default function SiteRow(props: Props) {
  const {site, pinned, index, count, panel, drag} = props;
  const notes = site.note_count;
  return (
    <li className="site-item" data-site-id={site.id} data-over={drag?.over || undefined}>
      <div
        className="site-row"
        draggable={pinned && !!drag}
        onDragStart={drag?.onDragStart}
        onDragOver={drag?.onDragOver}
        onDrop={drag?.onDrop}
        onDragEnd={drag?.onDragEnd}
      >
        <button
          type="button"
          className="site-open"
          title={site.start_url}
          aria-keyshortcuts={pinned ? 'Alt+ArrowUp Alt+ArrowDown' : undefined}
          onClick={props.onOpen}
          onKeyDown={event => {
            if (!pinned || !event.altKey) return;
            if (event.key === 'ArrowUp') {
              event.preventDefault();
              props.onMove(-1);
            } else if (event.key === 'ArrowDown') {
              event.preventDefault();
              props.onMove(1);
            }
          }}
        >
          <Globe size={16} strokeWidth={1.5} aria-hidden="true" />
          <span className="site-title">{site.title}</span>
          <span className="site-origin">{originLabel(site.origin)}</span>
        </button>
        <span className="site-actions">
          <Button
            variant="ghost"
            size="icon"
            className="site-action site-notes-toggle"
            data-active={notes > 0 || undefined}
            aria-label={'Notes for ' + site.title + (notes ? ', ' + notes : '')}
            aria-expanded={panel === 'notes'}
            onClick={() => props.onPanel(panel === 'notes' ? null : 'notes')}
          >
            <StickyNote size={16} strokeWidth={1.5} aria-hidden="true" />
            {notes > 0 && <span className="site-note-badge">{notes}</span>}
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="site-action" aria-label={'Actions for ' + site.title}>
                <Ellipsis size={16} strokeWidth={1.5} aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={props.onOpen}>Open</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => props.onPanel('edit')}>Edit title and open mode</DropdownMenuItem>
              {pinned && index > 0 && <DropdownMenuItem onSelect={() => props.onMove(-1)}>Move up</DropdownMenuItem>}
              {pinned && index < count - 1 && <DropdownMenuItem onSelect={() => props.onMove(1)}>Move down</DropdownMenuItem>}
              <DropdownMenuSeparator />
              {pinned ? (
                <DropdownMenuItem onSelect={props.onUnpin}>Unpin</DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={props.onPin}>Pin</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => props.onPanel('remove')}>Remove site</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </span>
      </div>
      {panel === 'notes' && <SiteNotes site={site} onChanged={props.onNotesChanged} />}
      {panel === 'edit' && <SiteEditForm site={site} onSave={props.onEdit} onCancel={() => props.onPanel(null)} />}
      {panel === 'remove' && (
        <div className="site-confirm" role="group" aria-label={'Remove ' + site.title}>
          <p>
            Remove {site.title}?{notes > 0 ? (notes === 1 ? ' Its note is deleted too.' : ' Its ' + notes + ' notes are deleted too.') : ''}
          </p>
          <div className="site-edit-actions">
            <Button variant="destructive" size="sm" onClick={props.onRemove}>
              Remove site
            </Button>
            <Button variant="ghost" size="sm" onClick={() => props.onPanel(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
