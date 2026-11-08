/** Plain text for the voice: markdown marks, links, code and tables are not read aloud. */
export const MAX_SPEECH_CHARS = 8000;

export function speechText(markdown: string): string {
  const lines = markdown
    .replace(/```[\s\S]*?```/g, '\n\u0000CODE\u0000\n')
    .replace(/~~~[\s\S]*?~~~/g, '\n\u0000CODE\u0000\n')
    .split('\n')
    .filter(line => !/^\s*\|/.test(line) && !/^\s*[-:| ]{3,}\s*$/.test(line))
    .map(line =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/, '')
        .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')
        .replace(/^\s*>\s?/, '')
        .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
        .replace(/<[^>]+>[^<]*<\/[^>]+>|<[^>]+>/g, ' ')
        .replace(/`([^`]*)`/g, '$1')
        .replace(/(\*\*|__|\*|~~)/g, '')
        .replace(/(^|\s)_([^_]+)_(?=\s|[.,;:!?]|$)/g, '$1$2')
        .trim(),
    )
    .map(line => (line === '\u0000CODE\u0000' ? 'Code omitted.' : line))
    .filter(Boolean);
  const text = lines
    .map(line => (/[.!?:…]$/.test(line) ? line : line + '.'))
    .join(' ')
    .replace(/:\./g, ':')
    .replace(/\s+/g, ' ')
    .trim();
  return text.slice(0, MAX_SPEECH_CHARS);
}
