import {useSyncExternalStore} from 'react';

/**
 * The page titles of built-in browser tabs, so a browser tab of the layout can be named after its page. The browser surface
 * writes the title of the tab it shows; the tab strip and the phone's tab switcher read it. It holds titles only.
 */

let titles: ReadonlyMap<string, string> = new Map();
const listeners = new Set<() => void>();

export function setBrowserTabTitle(id: string, title: string) {
  if (titles.get(id) === title) return;
  const next = new Map(titles);
  if (title) next.set(id, title);
  else next.delete(id);
  titles = next;
  listeners.forEach(listener => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The current titles. The map changes identity whenever one title does. */
export function useBrowserTabTitles(): ReadonlyMap<string, string> {
  return useSyncExternalStore(subscribe, () => titles, () => titles);
}
