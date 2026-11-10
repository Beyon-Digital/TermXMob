import {useState} from 'react';
import {request} from '../lib/api';
import {usePolling} from '../lib/use-polling';

/**
 * How many things the owner's bots are waiting on them for, for the badge on the Bots entry in Settings navigation.
 * It polls the same `needs_you` query the Bots screen uses, and stops while the tab is hidden or `enabled` is false.
 * The workspace calls it once and hands the count to both the sidebar Settings button and Settings navigation. If the query fails
 * (no access, host unreachable) the last known count stays; there is no badge before the first answer.
 */
export function useBotsNeedYouCount(enabled = true): number {
  const [count, setCount] = useState(0);
  usePolling(
    async () => {
      const result = await request<{data?: {needs_you?: unknown[]}; errors?: unknown[]}>(
        '/graphql', {method: 'POST', body: JSON.stringify({query: 'query { needs_you { id } }'})});
      if (result?.errors?.length || !Array.isArray(result?.data?.needs_you)) throw new Error('needs_you unavailable');
      return result.data.needs_you.length;
    },
    {interval: 30000, maxInterval: 120000, enabled, onResult: setCount, onError: () => {}},
  );
  return count;
}
