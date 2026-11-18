import {Globe, Plus} from 'lucide-react';
import {useState, type DragEvent} from 'react';
import {Button} from '../../components/ui/button';
import AddSiteForm from '../../features/sites/AddSiteForm';
import SiteRow, {type SitePanel} from '../../features/sites/SiteRow';
import {useSites} from '../../features/sites/use-sites';
import {originLabel, type Site} from '../../lib/sites';
import '../../features/sites/sites.css';
import './views.css';

type Props = {
  /** The project the workspace is on (its browser profile is the default for a new site). */
  projectId: string;
  /** Show a browser tab in the editor area. */
  onOpenTab: (tabId: string) => void;
};

/**
 * The Sites side bar view: pinned sites in the owner's order, sites that are saved but not pinned, suggestions from what
 * was visited often, and the browser tabs that are open. "Add site" is the one primary action.
 */
export default function SitesView({projectId, onOpenTab}: Props) {
  const state = useSites(true);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<{id: string; panel: SitePanel} | null>(null);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const {pinned, unpinned, suggestions, tabs} = state;

  const panelOf = (site: Site): SitePanel => (open?.id === site.id ? open.panel : null);
  const setPanel = (site: Site, panel: SitePanel) => setOpen(panel ? {id: site.id, panel} : null);

  async function openSite(site: Site) {
    const result = await state.open(site);
    if (result) onOpenTab(result.tab.id);
  }

  async function move(from: number, to: number) {
    if (to < 0 || to >= pinned.length) return;
    const site = pinned[from];
    await state.move(from, to);
    setAnnouncement(site.title + ' moved to position ' + (to + 1) + ' of ' + pinned.length + '.');
    // Moving a row in the DOM drops keyboard focus, so put it back on the row that moved: it can be moved again at once.
    requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-site-id="' + site.id + '"] .site-open')?.focus());
  }

  function drag(index: number) {
    return {
      over: dragOver === index && dragFrom !== null && dragFrom !== index,
      onDragStart: (event: DragEvent) => {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', pinned[index].id);
        setDragFrom(index);
      },
      onDragOver: (event: DragEvent) => {
        if (dragFrom === null) return;
        event.preventDefault();
        setDragOver(index);
      },
      onDrop: (event: DragEvent) => {
        event.preventDefault();
        const from = dragFrom;
        setDragFrom(null);
        setDragOver(null);
        if (from !== null) void move(from, index);
      },
      onDragEnd: () => {
        setDragFrom(null);
        setDragOver(null);
      },
    };
  }

  const nothing = state.loaded && !pinned.length && !unpinned.length && !suggestions.length && !tabs.length;
  if (!state.available) {
    return <p className="view-empty view-empty-padded">Saved sites are not available for this sign-in.</p>;
  }

  const rows = (list: Site[], isPinned: boolean) => (
    <ul className="view-list site-list">
      {list.map((site, index) => (
        <SiteRow
          key={site.id}
          site={site}
          pinned={isPinned}
          index={index}
          count={list.length}
          panel={panelOf(site)}
          drag={isPinned ? drag(index) : undefined}
          onPanel={panel => setPanel(site, panel)}
          onOpen={() => void openSite(site)}
          onMove={delta => void move(index, index + delta)}
          onPin={() => void state.pin(site)}
          onUnpin={() => void state.unpin(site)}
          onRemove={() => {
            setOpen(null);
            void state.remove(site);
          }}
          onEdit={patch => state.edit(site, patch)}
          onNotesChanged={state.refresh}
        />
      ))}
    </ul>
  );

  return (
    <div className="sites-view">
      <div className="view-section view-section-first">
        <Button className="view-primary" aria-expanded={adding} onClick={() => setAdding(value => !value)}>
          <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
          Add site
        </Button>
        {adding && <AddSiteForm projectId={projectId} onAdd={state.add} onDone={() => setAdding(false)} />}
      </div>
      <p className="visually-hidden" role="status">
        {announcement}
      </p>
      {state.error && (
        <div className="view-error" role="alert">
          <span>{state.error}</span>
          <Button variant="ghost" size="sm" onClick={state.dismissError}>
            Dismiss
          </Button>
        </div>
      )}
      {!state.loaded && <p className="view-empty view-empty-padded">Loading sites…</p>}
      {nothing && <p className="view-empty view-empty-padded">No saved sites yet.</p>}
      {pinned.length > 0 && (
        <section className="view-section" aria-labelledby="sites-pinned">
          <h3 id="sites-pinned" className="view-heading">
            Pinned
            <span className="view-count">{pinned.length}</span>
          </h3>
          {rows(pinned, true)}
        </section>
      )}
      {unpinned.length > 0 && (
        <section className="view-section" aria-labelledby="sites-saved">
          <h3 id="sites-saved" className="view-heading">
            Saved
            <span className="view-count">{unpinned.length}</span>
          </h3>
          {rows(unpinned, false)}
        </section>
      )}
      {suggestions.length > 0 && (
        <section className="view-section" aria-labelledby="sites-suggested">
          <h3 id="sites-suggested" className="view-heading">
            Suggested
          </h3>
          <ul className="view-list site-list">
            {suggestions.map(tip => (
              <li key={tip.profile_id + tip.origin} className="site-item">
                <div className="site-row">
                  <span className="site-open site-static" title={tip.start_url}>
                    <Globe size={16} strokeWidth={1.5} aria-hidden="true" />
                    <span className="site-title">{tip.title}</span>
                    <span className="site-origin">{originLabel(tip.origin)}</span>
                  </span>
                  <Button variant="ghost" size="sm" className="site-pin" aria-label={'Pin ' + tip.title} onClick={() => void state.pinSuggestion(tip)}>
                    Pin
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {tabs.length > 0 && (
        <section className="view-section" aria-labelledby="sites-open">
          <h3 id="sites-open" className="view-heading">
            Open tabs
            <span className="view-count">{tabs.length}</span>
          </h3>
          <ul className="view-list site-list">
            {tabs.map(tab => (
              <li key={tab.id} className="site-item">
                <div className="site-row">
                  <button type="button" className="site-open site-tab" title={tab.url} onClick={() => onOpenTab(tab.id)}>
                    <Globe size={16} strokeWidth={1.5} aria-hidden="true" />
                    <span className="site-title">{tab.title || 'New tab'}</span>
                    <span className="site-origin">{tab.url === 'about:blank' ? '' : originLabel(tab.url.replace(/^(https?:\/\/[^/]+).*$/, '$1'))}</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
