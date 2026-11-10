const LINE_CHARS = 90;
const FALLBACK = 140;

/**
 * A starting height for a row the virtualizer has not measured yet. A closer guess means fewer scroll
 * jumps when real heights arrive. Assistant rows get a taller cap because they hold markdown and code.
 */
export function estimateMessageHeight(message: {role: string; text: string; task?: unknown} | undefined): number {
  if (!message) return FALLBACK;
  const breaks = message.text.split('\n').length - 1;
  const lines = Math.ceil(message.text.length / LINE_CHARS) + breaks;
  if (message.role === 'user') return 56 + Math.min(lines, 12) * 22;
  return 64 + Math.min(lines, 60) * 24 + (message.task ? 44 : 0);
}
