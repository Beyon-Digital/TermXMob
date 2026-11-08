/** schema.graphql `Bot`, `BotTemplate` and the wording the Bots screens share. JSON scalars are narrowed to what the screens read. */

export type BotState = 'active' | 'paused' | 'archived';

export interface BotCeiling { tools: string[]; approval_mode: string; sandbox_profile: string; deny_capabilities: string[] }
export interface BotRule { id: string; effect: string; action_type: string; tool: string; display: string; capabilities?: string[]; created_at?: number; times_used?: number; expires_at?: number | null }
export interface BotTask { id: string; status: string; updated_at: number }

export interface Bot {
  id: string; revision: number; name: string; state: BotState; template?: string | null;
  description: string; instructions: string; project_id?: string | null; conversation_id: string; custom_agent_id: string;
  model?: string | null; provider_id?: string | null; tools: string[]; approval_mode?: string | null; sandbox_profile?: string | null;
  limits: Record<string, number>; ceiling: BotCeiling; peers: string[];
  effective?: { tools: string[]; approval_mode: string; sandbox_profile: string; deny_capabilities: string[] } | null;
  violations: string[]; rules: BotRule[]; needs_you: number; active_tasks: number; last_task?: BotTask | null;
  created_by: string; config_changed_at?: number | null; created_at: number; updated_at: number;
}

export interface BotTemplate { id: string; name: string; description: string; requires_project: boolean; tools: string[]; instructions: string }
export interface ProjectChoice { id: string; name: string; path?: string }

/** The tools a bot can use now: its own list, held to the ceiling when the host has worked that out. */
export const botTools = (bot: Pick<Bot, 'tools' | 'effective'>) => bot.effective?.tools || bot.tools;

export const BOT_FIELDS = 'id revision name state template description instructions project_id conversation_id custom_agent_id model provider_id tools approval_mode sandbox_profile limits ceiling peers effective violations rules needs_you active_tasks last_task created_by config_changed_at created_at updated_at';
export const TEMPLATE_FIELDS = 'id name description requires_project tools instructions';

export const stateLabel: Record<BotState, string> = { active: 'Active', paused: 'Paused', archived: 'Deleted' };

/**
 * Wording for the tools a bot can be given, grouped by what they let it do. This is only labels: which tools exist comes from the
 * host (`agent_tool_names`), and the host validates every name again. A tool without wording here is listed under its own name.
 */
export const toolGroups: { label: string; tools: { id: string; label: string }[] }[] = [
  { label: 'Read the project', tools: [
    { id: 'read_file', label: 'Read files' }, { id: 'list_files', label: 'List files' }, { id: 'search_project', label: 'Search the project' },
    { id: 'git_status', label: 'See git status' }, { id: 'git_diff', label: 'See git changes' }, { id: 'memory_read', label: 'Read its memory' }, { id: 'read_skill', label: 'Read skills on demand' }] },
  { label: 'Change the project', tools: [
    { id: 'write_file', label: 'Write files' }, { id: 'apply_patch', label: 'Apply patches' }, { id: 'git_stage', label: 'Stage changes' },
    { id: 'git_commit', label: 'Commit changes' }, { id: 'git_branch', label: 'Manage branches' }] },
  { label: 'Run commands', tools: [
    { id: 'run_check', label: 'Run checks' }, { id: 'run_shell', label: 'Run shell commands' }, { id: 'run_runbook', label: 'Run saved runbooks' }] },
  { label: 'Reach outside', tools: [
    { id: 'git_fetch', label: 'Fetch from remotes' }, { id: 'git_pull', label: 'Pull from remotes' }, { id: 'git_push', label: 'Push to remotes' },
    { id: 'mcp_call', label: 'Call connected tools' }, { id: 'share_file', label: 'Share files in chat' }, { id: 'memory_write', label: 'Write to its memory' },
    { id: 'computer', label: 'Control this computer' }] },
  { label: 'Message others', tools: [
    { id: 'send_message', label: 'Send messages' }, { id: 'read_inbox', label: 'Read its inbox' }, { id: 'mark_message', label: 'Mark messages read' }] },
];
/** Used only when the host's list of tool names cannot be loaded. */
export const fallbackToolIds = toolGroups.flatMap(group => group.tools.map(tool => tool.id));
export const toolLabel = (id: string) => toolGroups.flatMap(group => group.tools).find(tool => tool.id === id)?.label || id;
/** A readable name for a tool this screen has no wording for: `spawn_subagent` is "Spawn subagent", `mcp__files__list_dir` is "Files: list dir". */
export function plainToolLabel(id: string) {
  const words = (value: string) => value.replace(/[_.\-]+/g, ' ').replace(/\s+/g, ' ').trim();
  const sentence = (value: string) => (value ? value[0].toUpperCase() + value.slice(1) : id);
  const parts = id.split('__').filter(Boolean);
  if (parts[0] === 'mcp' && parts.length >= 3) return sentence(words(parts[1])) + ': ' + words(parts.slice(2).join(' '));
  return sentence(words(id));
}

/** Capability denies the ceiling can hold, each a set of capability names. */
export const denyGroups: { id: string; label: string; description: string; capabilities: string[] }[] = [
  { id: 'network', label: 'Network access', description: 'Reaching other machines or the internet.', capabilities: ['net.outbound:any'] },
  { id: 'publish', label: 'Publishing', description: 'Pushing code, publishing packages or submitting forms.', capabilities: ['git.publish', 'package.publish', 'external.submit'] },
  { id: 'computer', label: 'Computer control', description: 'Driving the mouse, keyboard and apps.', capabilities: ['computer.control'] },
];
export const denied = (ceiling: BotCeiling, group: { capabilities: string[] }) => group.capabilities.every(name => ceiling.deny_capabilities.includes(name));

export const approvalModes = [
  { id: 'standard', label: 'Ask for every risky action' }, { id: 'remember', label: 'Remember approvals' }, { id: 'autonomous', label: 'Approve safe actions itself' },
];
export const sandboxProfiles = [
  { id: 'agent', label: 'Agent sandbox (most limited)' }, { id: 'workspace', label: 'Workspace sandbox' }, { id: 'host', label: 'This computer (least limited)' },
];
export const approvalRank = (value?: string | null) => approvalModes.findIndex(row => row.id === value);
export const sandboxRank = (value?: string | null) => sandboxProfiles.findIndex(row => row.id === value);

export const taskStatusLabel = (status?: string | null) => ({
  completed: 'Finished', running: 'Running', awaiting_approval: 'Needs you', failed: 'Failed', cancelled: 'Stopped', paused: 'Paused', planning: 'Planning',
} as Record<string, string>)[status || ''] || (status ? status.replace(/_/g, ' ') : 'No runs yet');
