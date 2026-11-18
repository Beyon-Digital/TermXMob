import {useCallback, useMemo, useRef, useState} from 'react';
import {useGraphqlSubscription} from '../../lib/graphql-subscription';
import {
  SITE_EVENTS_QUERY,
  applySiteEvent,
  listBrowserTabs,
  listSites,
  listSuggestions,
  openSite,
  otherSites,
  pinnedSites,
  removeSite,
  reorderPins,
  saveSite,
  updateSite,
  type BrowserTabInfo,
  type Site,
  type SiteEvent,
  type SiteSuggestion,
} from '../../lib/sites';
import {usePolling} from '../../lib/use-polling';

const DEAD = ['closed', 'crashed'];

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/**
 * The saved sites of the signed-in owner: the list, the suggestions, and which browser tabs are open.
 *
 * The `browser_site_events` subscription keeps the list current while it is live. A slow poll stays as the fallback (the
 * native shell has no socket) and refreshes the parts the subscription does not carry: suggestions and open tabs. A host
 * that does not offer saved sites, or a token without the browser scope, leaves the list empty and `available` false.
 */
export function useSites(enabled: boolean) {
  const [sites, setSites] = useState<Site[]>([]);
  const [suggestions, setSuggestions] = useState<SiteSuggestion[]>([]);
  const [tabs, setTabs] = useState<BrowserTabInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [available, setAvailable] = useState(true);
  const [error, setError] = useState('');
  const [live, setLive] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const writing = useRef(0);

  useGraphqlSubscription<SiteEvent>(
    {
      query: SITE_EVENTS_QUERY,
      field: 'browser_site_events',
      onEvent: event => setSites(current => applySiteEvent(current, event)),
      onLive: setLive,
    },
    enabled,
  );

  usePolling(
    async () => {
      const [rows, tips, open] = await Promise.all([
        listSites(),
        listSuggestions(5).catch(() => [] as SiteSuggestion[]),
        listBrowserTabs().catch(() => [] as BrowserTabInfo[]),
      ]);
      return {rows, tips, open};
    },
    {
      interval: live ? 20000 : 6000,
      backoff: false,
      enabled,
      resetKey,
      onResult: ({rows, tips, open}) => {
        if (writing.current) return;
        setSites(rows);
        setSuggestions(tips);
        setTabs(open.filter(tab => !DEAD.includes(tab.state)));
        setLoaded(true);
        setAvailable(true);
        setError('');
      },
      onError: reason => {
        const text = message(reason);
        setLoaded(true);
        if (/forbidden|missing browser authority|403/i.test(text)) setAvailable(false);
        else setError(text);
      },
    },
  );

  const refresh = useCallback(() => setResetKey(key => key + 1), []);

  /** Run a write, keeping the poll from overwriting an optimistic list while it is in flight. */
  const write = useCallback(
    async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
      writing.current++;
      try {
        const value = await work();
        setError('');
        return value;
      } catch (reason) {
        setError(message(reason));
        return undefined;
      } finally {
        writing.current--;
        refresh();
      }
    },
    [refresh],
  );

  const pinned = useMemo(() => pinnedSites(sites), [sites]);
  const unpinned = useMemo(() => otherSites(sites), [sites]);

  return {
    sites,
    pinned,
    unpinned,
    suggestions,
    tabs,
    loaded,
    available,
    error,
    refresh,
    dismissError: () => setError(''),
    pinSuggestion: (tip: SiteSuggestion) =>
      write(() => saveSite({profile_id: tip.profile_id, url: tip.start_url, title: tip.title, pinned: true})),
    add: (input: {profile_id: string; url: string; title?: string}) => write(() => saveSite({...input, pinned: true})),
    pin: (site: Site) => write(() => updateSite(site.id, {pinned: true})),
    unpin: (site: Site) => write(() => updateSite(site.id, {pinned: false})),
    remove: (site: Site) => write(() => removeSite(site.id)),
    edit: (site: Site, patch: Partial<Pick<Site, 'title' | 'open_mode'>>) => write(() => updateSite(site.id, patch)),
    open: (site: Site) => write(() => openSite(site.id)),
    /** Move the pinned site at `from` to `to`. The list changes at once; the host stores the new positions. */
    move: async (from: number, to: number) => {
      const changes = reorderPins(pinned, from, to);
      if (!changes.length) return;
      setSites(current => current.map(site => {
        const change = changes.find(item => item.id === site.id);
        return change ? {...site, position: change.position} : site;
      }));
      await write(async () => {
        for (const change of changes) await updateSite(change.id, {position: change.position});
      });
    },
  };
}

export type SitesState = ReturnType<typeof useSites>;
