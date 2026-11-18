import {safeWebLink} from '../../lib/content-preview';

/** Opens a link printed in the terminal. Only plain http and https addresses are followed, and only on Command-click (Control-click off macOS). */
export function openTerminalLink(event: Pick<MouseEvent, 'metaKey' | 'ctrlKey'>, uri: string): boolean {
  if (!event.metaKey && !event.ctrlKey) return false;
  const href = safeWebLink(uri);
  if (!href) return false;
  // The same mechanism the rest of the workspace uses: a new browsing context with no opener, which the desktop shell hands to the system browser.
  const popup = window.open(href, '_blank', 'noopener,noreferrer');
  if (popup) {
    try { popup.opener = null; } catch { /* some webviews restrict opener access */ }
  }
  return true;
}
