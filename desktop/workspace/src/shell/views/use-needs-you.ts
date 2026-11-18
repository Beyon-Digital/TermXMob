import {useState} from 'react';
import {gql} from '../../lib/api';
import type {NeedsYouItem} from '../../lib/bot-schedules';
import {usePolling} from '../../lib/use-polling';

const QUERY = 'query{needs_you{id kind bot_id bot_name title detail scheduled}}';

/**
 * What the owner's bots are waiting on them for, as rows for the Agents view. It polls the same `needs_you` query the Bots
 * screen uses and stops while the view is not showing. Text in an item was written by a model or an earlier run, so the view
 * shows it as plain text and never acts on it; answering happens in Settings.
 */
export function useNeedsYouItems(enabled: boolean): NeedsYouItem[] {
  const [items, setItems] = useState<NeedsYouItem[]>([]);
  usePolling(async () => (await gql<{needs_you: NeedsYouItem[]}>(QUERY)).needs_you ?? [], {
    interval: 30000,
    maxInterval: 120000,
    enabled,
    onResult: setItems,
    onError: () => {},
  });
  return items;
}
