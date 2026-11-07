/** Shows a stored binding such as "Mod+Alt+B" with the keys of the user's platform. */
export function formatShortcut(binding: string, mac = isMac()) {
  const parts = binding.split('+').map(part => part.trim()).filter(Boolean);
  if (mac) {
    const symbols: Record<string, string> = {mod: '⌘', meta: '⌘', cmd: '⌘', ctrl: '⌃', control: '⌃', alt: '⌥', option: '⌥', shift: '⇧'};
    return parts.map(part => symbols[part.toLowerCase()] || (part.length === 1 ? part.toUpperCase() : part)).join('');
  }
  const names: Record<string, string> = {mod: 'Ctrl', meta: 'Win', cmd: 'Win', control: 'Ctrl'};
  return parts.map(part => names[part.toLowerCase()] || (part.length === 1 ? part.toUpperCase() : part)).join('+');
}

function isMac() {
  return typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');
}
