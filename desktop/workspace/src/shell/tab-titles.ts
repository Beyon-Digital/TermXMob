import {KIND_LABEL} from '../components/shell/tab-meta';
import {diffTitle} from '../features/scm/diff-ref';
import type {Tab} from '../lib/layout/model';

type Facts = {
  /** Every open tab, for numbering terminals. */
  tabs: readonly Tab[];
  primaryId: string;
  selected: string;
  /** The title of the selected conversation. */
  sessionTitle: string | undefined;
  /** The open file for a buffer key: its path, and whether it is a recovered copy. */
  file: (key: string) => {path: string; recovered: boolean} | undefined;
  /** What a CLI session is called, by session id. */
  cli?: (id: string) => string | undefined;
  /** The page title of a built-in browser tab, by tab id. */
  browser?: (id: string) => string | undefined;
};

const fileName = (path: string) => path.split('/').filter(Boolean).at(-1) ?? path;

/** The file name in a buffer key (`project:folder:path`) for a tab whose buffer has not loaded yet. */
const keyFileName = (key: string) => fileName(key).split(':').at(-1) ?? key;

/** What a tab is called: the strip, the phone's top bar and its tab switcher all use it. */
export function tabTitle(tab: Tab, facts: Facts): string {
  switch (tab.kind) {
    case 'chat':
      return facts.primaryId && facts.selected === facts.primaryId ? 'Main agent' : facts.sessionTitle || 'Chat';
    case 'file': {
      const buffer = tab.ref ? facts.file(tab.ref) : undefined;
      if (!buffer) return tab.ref ? keyFileName(tab.ref) : KIND_LABEL.file;
      return fileName(buffer.path) + (buffer.recovered ? ' (recovered copy)' : '');
    }
    case 'diff':
      return diffTitle(tab.ref);
    case 'terminal': {
      const index = facts.tabs.filter(item => item.kind === 'terminal').findIndex(item => item.id === tab.id);
      return index > 0 ? KIND_LABEL.terminal + ' ' + (index + 1) : KIND_LABEL.terminal;
    }
    case 'cli':
      return (tab.ref && facts.cli?.(tab.ref)) || KIND_LABEL.cli;
    case 'browser':
      return (tab.ref && facts.browser?.(tab.ref)) || KIND_LABEL.browser;
    default:
      return KIND_LABEL[tab.kind];
  }
}
