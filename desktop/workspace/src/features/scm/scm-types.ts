import type {SessionChangeFile} from '../../lib/session-changes';

export type Worktree = {id: string; path: string; branch: string};

export type DeliveryMeta = {
  branch?: string;
  ahead: number;
  behind: number;
  remotes: string[];
  worktrees: Worktree[];
};

/** One file a discard would change, as the host's dry run lists it. */
export type DiscardFile = {
  path: string;
  /** modified and deleted are restored; staged is restored from HEAD; untracked is deleted. */
  kind: 'modified' | 'deleted' | 'staged' | 'untracked';
  staged: boolean;
  worktree: boolean;
  /** The file also has staged changes that this discard leaves alone. */
  keeps_staged: boolean;
  origin?: string;
};

/** The dry run of a discard: exactly what would be lost, and what is already clean. */
export type DiscardReview = {
  files: DiscardFile[];
  folders: string[];
  already_clean: string[];
};

/** A delivery operation the host has recorded but not run, or run. Publishing and destructive ones wait for an exact confirmation. */
export type Prepared = {
  id: string;
  operation: string;
  arguments: Record<string, unknown>;
  head: string;
  requires_confirmation: boolean;
  /** Only a discard carries one. */
  review?: DiscardReview;
};

export type ActionRecord = {
  id: string;
  operation: string;
  arguments: string;
  head: string;
  status: string;
  created: number;
  result: string | null;
};

export type PullRequest = {
  number: number;
  title: string;
  body: string;
  url: string;
  state: string;
  mergeable: string;
  headRefName: string;
  baseRefName: string;
  statusCheckRollup: {name?: string; context?: string; state?: string; status?: string; conclusion?: string; detailsUrl?: string; targetUrl?: string}[];
  comments: {body: string; url?: string}[];
  reviews: {body: string; state: string}[];
};

export type Side = 'staged' | 'worktree';

/** One changed file as a row in a group: the side it is listed on, its letter and what the letter means. */
export type ScmEntry = {
  key: string;
  path: string;
  name: string;
  dir: string;
  side: Side | 'conflict';
  letter: string;
  word: string;
  file: SessionChangeFile;
};
