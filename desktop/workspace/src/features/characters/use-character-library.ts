import {useCallback, useEffect, useRef, useState} from 'react';
import {gql, request, type Provider} from '../../lib/api';
import {companionCapabilities, type CompanionCapabilities} from '../../lib/companion-native';
import {
  errorMessage,
  isRunning,
  keepIfSame,
  parseCharacterLibrary,
  parseCompanionPets,
  parseCompanionPreferences,
  updateCompanions,
  type CharacterLibrary,
  type CompanionPet,
  type CompanionPreferences,
} from '../../lib/characters';
import {usePolling} from '../../lib/use-polling';

const POLL_MS = 2500;

/** Builds a preference change from the newest saved preferences. */
export type SavePreferences = (build: (current: CompanionPreferences) => Partial<CompanionPreferences>) => Promise<void>;

interface Snapshot {
  library: CharacterLibrary;
  pets: CompanionPet[];
  /** How many preference saves had finished when the fetch started. */
  saves: number;
}

/**
 * Everything the studio reads from the host: the library, the pets, native capabilities and image accounts.
 * The library is refetched when the host announces a change or the tab becomes visible, and on a timer
 * only while a creation operation is running.
 */
export function useCharacterLibrary() {
  const [library, setLibrary] = useState<CharacterLibrary | null>(null);
  const [pets, setPets] = useState<CompanionPet[]>([]);
  const [loadError, setLoadError] = useState('');
  const [capabilities, setCapabilities] = useState<CompanionCapabilities | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerError, setProviderError] = useState('');
  const latest = useRef<CharacterLibrary | null>(null);
  const flight = useRef<Promise<void> | null>(null);
  const alive = useRef(true);
  const saving = useRef(0);
  const saved = useRef(0);

  const fetchSnapshot = useCallback(async (signal?: AbortSignal): Promise<Snapshot> => {
    const saves = saved.current;
    const [library, runtime] = await Promise.all([
      request<unknown>('/api/characters', {signal}),
      request<unknown>('/api/companions/runtime', {signal}),
    ]);
    return {library: parseCharacterLibrary(library), pets: parseCompanionPets(runtime), saves};
  }, []);

  const commit = useCallback((next: CharacterLibrary) => {
    // The ref moves first so a queued preference change reads what the previous one just saved.
    latest.current = keepIfSame(latest.current, next);
    setLibrary(latest.current);
  }, []);

  const apply = useCallback(
    (snapshot: Snapshot) => {
      if (!alive.current) return;
      // A reply fetched before a save finished, or while one is running, has older preferences than we hold.
      const stale = latest.current !== null && (saving.current > 0 || snapshot.saves !== saved.current);
      commit(stale && latest.current ? {...snapshot.library, preferences: latest.current.preferences} : snapshot.library);
      setPets(previous => keepIfSame(previous, snapshot.pets));
      setLoadError('');
    },
    [commit],
  );

  /** Refetches the library. Failures land in `loadError` instead of rejecting, so a finished save never reports a refresh problem as its own. */
  const reload = useCallback(
    async (fresh = true) => {
      if (flight.current) {
        await flight.current;
        if (!fresh) return;
      }
      const pending = fetchSnapshot()
        .then(apply)
        .catch(reason => {
          if (alive.current) setLoadError(errorMessage(reason));
        });
      flight.current = pending;
      try {
        await pending;
      } finally {
        if (flight.current === pending) flight.current = null;
      }
    },
    [apply, fetchSnapshot],
  );

  const loadProviders = useCallback(async () => {
    setProviderError('');
    try {
      const value = await gql<{agent_providers: Provider[]}>('{agent_providers{id name model capabilities secret_configured}}');
      if (!alive.current) return;
      const rows = Array.isArray(value.agent_providers) ? value.agent_providers : [];
      setProviders(rows.filter(row => row.capabilities.includes('image') && row.secret_configured));
    } catch (reason) {
      if (alive.current) setProviderError(errorMessage(reason));
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void reload(false);
    void loadProviders();
    companionCapabilities()
      .then(value => {
        if (alive.current) setCapabilities(value);
      })
      .catch(() => {
        if (alive.current) setCapabilities({desktop: false, backend: 'in-app'});
      });
    const changed = (event: Event) => setCapabilities((event as CustomEvent<CompanionCapabilities>).detail);
    window.addEventListener('termx-companion-capabilities', changed);
    return () => {
      alive.current = false;
      window.removeEventListener('termx-companion-capabilities', changed);
    };
  }, [reload, loadProviders]);

  const running = library?.operations.some(isRunning) ?? false;

  useEffect(() => {
    const refresh = () => void reload(false);
    const visible = () => {
      if (!document.hidden) refresh();
    };
    window.addEventListener('termx-companions-changed', refresh);
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.removeEventListener('termx-companions-changed', refresh);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [reload]);

  usePolling(fetchSnapshot, {
    interval: POLL_MS,
    enabled: running,
    backoff: false,
    immediate: false,
    onResult: apply,
    onError: reason => {
      if (alive.current) setLoadError(errorMessage(reason));
    },
  });

  /** Saves a preference change built from the newest saved preferences, so queued changes never overwrite each other. */
  const savePreferences = useCallback<SavePreferences>(
    async build => {
      const current = latest.current;
      if (!current) throw new Error('Your characters are still loading.');
      saving.current++;
      try {
        const next = parseCompanionPreferences(await updateCompanions(build(current.preferences)));
        saved.current++;
        if (latest.current) commit({...latest.current, preferences: next});
      } finally {
        saving.current--;
      }
    },
    [commit],
  );

  return {library, pets, loadError, capabilities, providers, providerError, running, reload, loadProviders, savePreferences};
}
