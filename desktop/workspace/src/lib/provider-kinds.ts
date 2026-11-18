/** Provider account kinds the host accepts, and which features each one supports. */

export const ANTHROPIC_KIND = 'anthropic';
export const ANTHROPIC_URL = 'https://api.anthropic.com';
/** Offered when an Anthropic account is saved with no model chosen. The first one is the default. */
export const ANTHROPIC_MODELS = ['claude-opus-5-5', 'claude-fable-5-2', 'claude-sonnet-5-5', 'claude-haiku-5-5'];

/** Kinds that speak an OpenAI wire (Responses or Chat Completions). */
export const OPENAI_KINDS = ['openai', 'openai-compatible'];

/**
 * Kinds that can be edited in Settings. ChatGPT accounts are managed through their own sign-in flow.
 */
export const EDITABLE_KINDS = [...OPENAI_KINDS, ANTHROPIC_KIND];

/**
 * Kinds usable where the host only speaks the OpenAI wire: dedicated runners, the browser reviewer,
 * live voice and the media studio. Anthropic accounts power the TermX internal engine only.
 */
export const OPENAI_WIRE_ONLY_KINDS = OPENAI_KINDS;

export const isAnthropic = (kind?: string | null) => kind === ANTHROPIC_KIND;

/** The "Provider" choices when adding an account. `url` is the address the account uses; custom has none. */
export const PROVIDER_CHOICES = [
  {id: 'openai', label: 'OpenAI', url: 'https://api.openai.com/v1'},
  {id: ANTHROPIC_KIND, label: 'Anthropic (Claude)', url: ANTHROPIC_URL},
  {id: 'openrouter', label: 'OpenRouter', url: 'https://openrouter.ai/api/v1'},
  {id: 'kie', label: 'Kie.ai media adapter', url: 'https://api.kie.ai'},
  {id: 'custom', label: 'Another compatible provider', url: ''},
];

const LABELS: Record<string, string> = {
  openai: 'OpenAI',
  'openai-compatible': 'OpenAI-compatible',
  anthropic: 'Anthropic (Claude)',
  chatgpt: 'ChatGPT',
};

/** Plain-language name for a provider kind; an unknown kind is shown as the host reported it. */
export const providerKindLabel = (kind: string) => LABELS[kind] ?? kind;
