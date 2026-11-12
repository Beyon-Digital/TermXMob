import {plainToolLabel, toolLabel as knownToolLabel} from '../../lib/bots';
import type {DraftKind} from './draft-api';

export interface SummaryRow { label: string; value: string; long?: boolean }

const toolLabel = (id: string) => { const label = knownToolLabel(id); return label === id ? plainToolLabel(id) : label; };
const text = (value: unknown) => (typeof value === 'string' ? value : '');
const list = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []);
const approval: Record<string, string> = {standard: 'Asks before risky actions', remember: 'Uses remembered approvals', autonomous: 'Acts without asking'};
const sandbox: Record<string, string> = {agent: 'Agent sandbox', workspace: 'Workspace sandbox', host: 'Host'};

export interface KindCopy {
  noun: string;
  title: string;
  /** Names the thing being made in the composer's accessible name. */
  composerLabel: string;
  /** One example of a good description, shown while the conversation is empty. */
  example: string;
  rows: (draft: Record<string, unknown>) => SummaryRow[];
}

export const kindCopy: Record<DraftKind, KindCopy> = {
  agent: {
    noun: 'agent', title: 'New agent', composerLabel: 'Describe the agent',
    example: 'An agent that reads a pull request and lists security risks, ordered by severity.',
    rows: draft => [
      {label: 'Name', value: text(draft.name)},
      {label: 'Purpose', value: text(draft.description)},
      {label: 'Instructions', value: text(draft.instructions), long: true},
      {label: 'Tools', value: list(draft.tools).map(toolLabel).join(', ') || 'None'},
      {label: 'Permissions', value: `${approval[text(draft.approval_mode)] || approval.standard}. ${sandbox[text(draft.sandbox_profile)] || sandbox.agent}.`},
    ],
  },
  bot: {
    noun: 'bot', title: 'New bot', composerLabel: 'Describe the bot',
    example: 'A bot that checks my project every morning and tells me what changed and what looks risky.',
    rows: draft => [
      {label: 'Name', value: text(draft.name)},
      {label: 'Description', value: text(draft.description)},
      {label: 'Instructions', value: text(draft.instructions), long: true},
      {label: 'Can use', value: list(draft.tools).map(toolLabel).join(', ') || 'Read-only tools'},
    ],
  },
  skill: {
    noun: 'skill', title: 'New skill', composerLabel: 'Describe the skill',
    example: 'A skill for writing release notes from the git log, grouped by feature and fix.',
    rows: draft => [
      {label: 'Folder name', value: text(draft.name)},
      {label: 'Use it when', value: text(draft.description)},
      {label: 'SKILL.md', value: text(draft.markdown), long: true},
    ],
  },
  character: {
    noun: 'character', title: 'Create a character', composerLabel: 'Describe the character',
    example: 'A small moss-green creature with round ears and a calm, curious personality.',
    rows: draft => [
      {label: 'Name', value: text(draft.name)},
      {label: 'Description', value: text(draft.prompt), long: true},
    ],
  },
};

export const asText = text;
export const asList = list;
