import {Search} from 'lucide-react';
import {useEffect, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import type {Project, Session} from '../../lib/api';
import {searchFiles, type SearchHit} from '../editor/file-io';
import '../explorer/explorer.css';

type Props = {
  project: Project | null;
  session: Session | null;
  /** Open a result: the file in a tab, at the line when the host gave one. */
  onOpenFile: (path: string, line?: number) => void;
  onError: (reason: unknown) => void;
};

/**
 * Workspace text search in the side bar. It asks the host the same question the old editor's search tab asked
 * (`features/editor/file-io.ts`), and a result opens its file as a tab at that line.
 */
export default function SearchView({project, session, onOpenFile, onError}: Props) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const scope = useRef('');
  const key = JSON.stringify([project?.id, session?.id, session?.worktree_id]);

  // Results belong to the checkout they were found in.
  useEffect(() => {
    scope.current = key;
    setHits(null);
  }, [key]);

  async function run() {
    const text = query.trim();
    if (!project || !text) return;
    const mine = scope.current;
    setBusy(true);
    try {
      const found = await searchFiles({project, session}, text);
      if (scope.current === mine) setHits(found);
    } catch (reason) {
      if (scope.current === mine) onError(reason);
    } finally {
      setBusy(false);
    }
  }

  if (!project) return <p className="explorer-note">Open a project to search its files.</p>;
  return (
    <div className="search-view">
      <form
        className="search-form"
        role="search"
        onSubmit={event => {
          event.preventDefault();
          void run();
        }}
      >
        <Input aria-label="Search in files" placeholder="Search in files" value={query} onChange={event => setQuery(event.target.value)} />
        <Button type="submit" variant="ghost" size="icon" aria-label="Run search" disabled={busy || !query.trim()}>
          <Search size={16} strokeWidth={1.5} aria-hidden="true" />
        </Button>
      </form>
      <div className="search-results" aria-live="polite">
        {busy && <p className="explorer-note">Searching…</p>}
        {!busy && hits && !hits.length && <p className="explorer-note">No results for “{query.trim()}”.</p>}
        {!busy &&
          hits?.map((hit, index) => (
            <button key={index} type="button" className="search-hit" onClick={() => onOpenFile(hit.path, hit.line)}>
              <span className="search-hit-path">
                {hit.path}
                {hit.line ? ':' + hit.line : ''}
              </span>
              {hit.text && <small>{hit.text}</small>}
            </button>
          ))}
      </div>
    </div>
  );
}
