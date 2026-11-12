import {request} from '../../lib/api';

export type DraftKind = 'agent' | 'bot' | 'skill' | 'character';
export interface DraftMessage { role: 'user' | 'assistant'; text: string }
/** What the host returned for one request. `drafted` carries a draft; `needs_input` carries a question; `no_provider` means no model account is set up. */
export interface DraftResult {
  status: 'drafted' | 'needs_input' | 'no_provider';
  reply: string;
  draft: Record<string, unknown> | null;
  warnings: string[];
  provider_id: string | null;
  model: string | null;
}

/** Limits that the host enforces too. Stopping early here only saves a round trip. */
export const MAX_MESSAGES = 12;
export const MAX_MESSAGE_CHARS = 4000;

const mutation = 'mutation($i:DraftDefinitionInput!){draft_definition(input:$i){status reply draft warnings provider_id model}}';

export async function draftDefinition(
  input: {kind: DraftKind; messages: DraftMessage[]; currentDraft?: Record<string, unknown> | null; projectId?: string | null},
  signal?: AbortSignal,
): Promise<DraftResult> {
  const variables = {i: {kind: input.kind, messages: input.messages, current_draft: input.currentDraft ?? null, project_id: input.projectId ?? null}};
  const body = await request<{data?: {draft_definition: DraftResult}; errors?: {message: string}[]}>('/graphql', {method: 'POST', body: JSON.stringify({query: mutation, variables}), signal});
  if (body.errors?.length) throw new Error(body.errors.map(error => error.message).join('\n'));
  if (!body.data?.draft_definition) throw new Error('The host returned no draft. Try again.');
  return body.data.draft_definition;
}
