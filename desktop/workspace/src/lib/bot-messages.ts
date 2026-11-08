/** schema.graphql `BotMessage` and the trigger fields of `BotSchedule`, narrowed to what the Bots screens read. */

export type PartyKind = 'owner' | 'orchestrator' | 'bot';
export interface Party { kind: PartyKind; id?: string | null; name: string }
export type MessageKind = 'report' | 'request' | 'handoff' | 'reply';
export type MessageStatus = 'unread' | 'read' | 'done' | 'parked';

export interface BotMessage {
  id: string; thread_id: string; sender: Party; recipient: Party; kind: MessageKind; subject: string; body: string;
  task_ref?: string | null; source_task_id?: string | null; hops: number; status: MessageStatus; reason?: string | null;
  automatic: boolean; in_reply_to?: string | null; created_at: number; updated_at: number;
}

export type ReportTarget = { kind: PartyKind; id?: string | null };
export type Trigger =
  | { kind: 'time' } | { kind: 'message' } | { kind: 'webhook' }
  | { kind: 'bot_finished'; bot_id: string; on: 'completed' | 'any' }
  | { kind: 'file_change'; glob: string; debounce_s: number };
export type TriggerKind = Trigger['kind'];

export interface TriggerSchedule {
  id: string; revision: number; prompt: string; enabled: boolean; state: string; reason?: string | null; spec: { kind: string };
  trigger: Trigger; report_to: ReportTarget; webhook_path?: string | null; webhook_secret?: string | null;
  max_runs: number; runs_started: number; grant_expires_at?: number | null; needs_reauthorization: boolean;
}

export const BOT_MESSAGE_FIELDS = 'id thread_id sender recipient kind subject body task_ref source_task_id hops status reason automatic in_reply_to created_at updated_at';
export const TRIGGER_FIELDS = 'id revision prompt enabled state reason spec trigger report_to webhook_path webhook_secret max_runs runs_started grant_expires_at needs_reauthorization';

/** What a message sender is called on screen. The owner is "You" to themselves. */
export const partyName = (party: Party) => party.kind === 'owner' ? 'You' : party.name;

export const kindLabel: Record<MessageKind, string> = { report: 'Report', request: 'Request', handoff: 'Handoff', reply: 'Reply' };

export interface Thread {
  id: string; subject: string; messages: BotMessage[]; path: string[]; last: number; unread: number; held: number;
}

/** Messages grouped by thread, newest thread first. The path is who spoke to whom, in order, without repeating a name next to itself. */
export function groupThreads(messages: BotMessage[]): Thread[] {
  const byThread = new Map<string, BotMessage[]>();
  for (const message of messages) byThread.set(message.thread_id, [...(byThread.get(message.thread_id) || []), message]);
  return [...byThread.entries()].map(([id, rows]) => {
    const ordered = [...rows].sort((a, b) => a.created_at - b.created_at);
    const path: string[] = [];
    for (const row of ordered) for (const name of [partyName(row.sender), partyName(row.recipient)]) if (path[path.length - 1] !== name) path.push(name);
    return { id, subject: ordered[0].subject, messages: ordered, path, last: ordered[ordered.length - 1].created_at,
      unread: ordered.filter(row => row.status === 'unread' && row.recipient.kind === 'owner').length, held: ordered.filter(row => row.status === 'parked').length };
  }).sort((a, b) => b.last - a.last);
}

export const reportValue = (target: ReportTarget) => target.kind === 'bot' ? 'bot:' + target.id : target.kind;

/** Where a result can go: the owner, the main agent, or another of the owner's bots. */
export const reportOptions = (bots: { id: string; name: string; state: string }[], selfId: string) => [
  { value: 'orchestrator', label: 'Main agent' }, { value: 'owner', label: 'You' },
  ...bots.filter(bot => bot.id !== selfId && bot.state !== 'archived').map(bot => ({ value: 'bot:' + bot.id, label: bot.name })),
];

export function describeTrigger(trigger: Trigger, nameOf: (botId: string) => string): string {
  switch (trigger.kind) {
    case 'message': return 'When a message arrives';
    case 'bot_finished': return `When ${nameOf(trigger.bot_id)} ${trigger.on === 'any' ? 'finishes, however it ends' : 'finishes successfully'}`;
    case 'file_change': return `When files matching ${trigger.glob} change and stay quiet for ${trigger.debounce_s} seconds`;
    case 'webhook': return 'When a signed webhook is called';
    default: return 'On a schedule';
  }
}
