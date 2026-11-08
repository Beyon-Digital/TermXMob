/** schema.graphql `BotSchedule` and `NeedsYouItem`, narrowed to what the Bots screens read. */

import type { ReportTarget, Trigger } from './bot-messages';

export type NeedsYouKind = 'approval' | 'run' | 'schedule' | 'memory' | 'message';

export interface NeedsYouItem {
  id: string; kind: NeedsYouKind; bot_id: string; bot_name: string; title: string; detail: string; scheduled: boolean;
  task_id?: string | null; approval_id?: string | null; run_id?: string | null; schedule_id?: string | null; memory_id?: string | null; message_id?: string | null;
  created_at?: number | null;
}

export interface BotSchedule {
  id: string; revision: number; bot_id: string; conversation_id: string; prompt: string; enabled: boolean; state: string; reason?: string | null;
  next_run?: number | null; max_runs: number; runs_started: number; grant_expires_at?: number | null;
  needs_reauthorization: boolean; paused_by_bot: boolean;
  trigger?: Trigger; report_to?: ReportTarget; webhook_path?: string | null; webhook_secret?: string | null;
}

export const NEEDS_YOU_FIELDS = 'id kind bot_id bot_name title detail scheduled task_id approval_id run_id schedule_id memory_id message_id created_at';
export const BOT_SCHEDULE_FIELDS = 'id revision bot_id conversation_id prompt enabled state reason next_run max_runs runs_started grant_expires_at needs_reauthorization paused_by_bot trigger report_to webhook_path';
export const TRIGGER_SCHEDULE_FIELDS = 'id revision prompt enabled state reason spec trigger report_to webhook_path max_runs runs_started grant_expires_at needs_reauthorization';

/** How long a fresh authorization lasts. The host caps it at 90 days. */
export const authorizationDays = [7, 30, 90] as const;

export const needsYouCount = (count: number) => count === 1 ? '1 needs you' : `${count} need you`;
