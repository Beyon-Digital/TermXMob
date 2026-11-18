import type {SessionChangeFile} from '../../lib/session-changes';

export type Worktree = {id: string; path: string; branch: string};

export type DeliveryMeta = {
  branch?: string;
  ahead: number;
  behind: number;
  remotes: string[];
  worktrees: Worktree[];
};

/** A delivery operation the host has recorded but not run, or run. Publishing ones wait for an exact confirmation. */
export type Prepared = {
  id: string;
  operation: string;
  arguments: Record<string, unknown>;
  head: string;
  requires_confirmation: boolean;
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
