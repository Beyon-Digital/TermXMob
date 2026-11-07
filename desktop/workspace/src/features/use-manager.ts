import {useCallback,useEffect,useState} from 'react';
import {json,request} from '../lib/api';
import {useSettingsSave} from './SettingsSurface';

type Json = Record<string, unknown>;

export const scoped = (project: string | null) => project ? `?project_id=${encodeURIComponent(project)}` : '';
const messageOf = (reason: unknown, fallback = 'Something went wrong. Try again.') => reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : fallback;

export async function gql<T = Json>(query: string, variables: Json = {}): Promise<T> {
  const result = await request<{data?: T; errors?: {message: string}[]}>('/graphql', json('POST', {query, variables}));
  if (result.errors?.length) throw new Error(result.errors.map(e => e.message).join('\n'));
  return result.data as T;
}

/**
 * Loads one section's data. `load` is read when the section mounts and on `reload()`, so a section
 * whose inputs change should be remounted with a `key` rather than re-run in place.
 */
export function useManagerLoad<T>(load: () => Promise<T>) {
  const [state, setState] = useState<{data: T | null; error: string; loading: boolean}>({data: null, error: '', loading: true});
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState(prior => ({...prior, loading: true, error: ''}));
    load().then(data => { if (!cancelled) setState({data, error: '', loading: false}) })
      .catch(reason => { if (!cancelled) setState(prior => ({...prior, error: messageOf(reason), loading: false})) });
    return () => { cancelled = true };
  }, [version]);
  const reload = useCallback(() => setVersion(value => value + 1), []);
  return {...state, reload};
}

/**
 * Runs one settings write. It marks the write as saved for the surface's dirty tracking, reports the
 * outcome, and reloads the section. `mutate` resolves to `null` when the write failed.
 */
export function useManagerMutation(reload: () => void, onChanged?: () => void) {
  const beginSave = useSettingsSave();
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const mutate = useCallback(async <T,>(fn: () => Promise<T>, message: string, {refresh = true}: {refresh?: boolean} = {}): Promise<T | null> => {
    const saved = beginSave();
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await fn();
      if (refresh) saved();
      onChanged?.();
      setNotice(message);
      if (refresh) reload();
      // `null` is reserved for failure, so a write whose reply body is `null` still counts as done.
      return result ?? (undefined as T);
    } catch (reason) { setError(messageOf(reason, String(reason))); return null }
    finally { setBusy(false) }
  }, [beginSave, reload, onChanged]);
  const post = useCallback((path: string, value: unknown, message: string, method = 'POST') => mutate(() => request(path, json(method, value)), message), [mutate]);
  const remove = useCallback((path: string, message: string) => mutate(() => request(path, {method: 'DELETE'}), message), [mutate]);
  const report = useCallback((reason: unknown) => setError(messageOf(reason, 'Settings could not be updated.')), []);
  return {busy, error, notice, mutate, post, remove, setNotice, report};
}
export type Mutation = ReturnType<typeof useManagerMutation>;
