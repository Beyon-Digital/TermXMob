import {
  Boxes,
  Clapperboard,
  Database,
  Folder,
  GitCompareArrows,
  Globe,
  MessageSquare,
  Monitor,
  Settings,
  SquareTerminal,
  Workflow,
  FileCode,
  type LucideIcon,
} from 'lucide-react';
import type {TabKind} from '../../lib/layout/model';

/** Canonical names (plan: "Canonical names"). Workbench, Files & code, Review changes and Delivery are retired. */
export const KIND_LABEL: Record<TabKind, string> = {
  chat: 'Chat',
  file: 'Editor',
  diff: 'Changes',
  terminal: 'Terminal',
  cli: 'CLI session',
  browser: 'Browser',
  computer: 'Computer',
  artifacts: 'Artifacts',
  data: 'Data',
  studio: 'Studio',
  settings: 'Settings',
  projects: 'Projects',
  workflows: 'Workflows',
};

export const KIND_ICON: Record<TabKind, LucideIcon> = {
  chat: MessageSquare,
  file: FileCode,
  diff: GitCompareArrows,
  terminal: SquareTerminal,
  cli: SquareTerminal,
  browser: Globe,
  computer: Monitor,
  artifacts: Boxes,
  data: Database,
  studio: Clapperboard,
  settings: Settings,
  projects: Folder,
  workflows: Workflow,
};

/**
 * The kinds the `+` menu offers. `cli` is left out until the host can list CLI sessions (it has no content yet), and every
 * kind here is a singleton in this release: choosing one that is open reveals it.
 */
export const OPENABLE_KINDS: readonly TabKind[] = [
  'chat',
  'file',
  'diff',
  'terminal',
  'browser',
  'computer',
  'artifacts',
  'data',
  'studio',
  'projects',
  'workflows',
  'settings',
];
