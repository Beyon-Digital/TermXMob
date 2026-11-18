import {
  Boxes,
  Bug,
  Clapperboard,
  Database,
  Eye,
  Folder,
  GitCompareArrows,
  Globe,
  MessageSquare,
  Monitor,
  Settings,
  SquareTerminal,
  TriangleAlert,
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
  problems: 'Problems',
  preview: 'Preview',
  debug: 'Run and debug',
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
  problems: TriangleAlert,
  preview: Eye,
  debug: Bug,
};

/**
 * The kinds the `+` menu of an editor-area group offers. `cli` is left out until the host can list CLI sessions (it has no
 * content yet). Singletons reveal themselves when open; `file` opens Quick open and `terminal` is not here (it lives in the
 * bottom panel).
 */
export const OPENABLE_KINDS: readonly TabKind[] = [
  'chat',
  'file',
  'diff',
  'browser',
  'computer',
  'artifacts',
  'data',
  'studio',
  'projects',
  'workflows',
  'settings',
];

/** What the `+` menu of the bottom panel offers: a new terminal and the panel's other tabs. */
export const PANEL_OPENABLE_KINDS: readonly TabKind[] = ['terminal', 'problems', 'preview', 'debug'];
