/** Focus helpers for Data tabs. When the control that had focus goes away (a tab is hidden, a dialog or banner closes), focus falls to the page, so keyboard and screen reader users would start again from the top. */

/** True when nothing useful has focus. */
export const focusLost = () => !document.activeElement || document.activeElement === document.body;

/** Focus the heading of a tab panel (or the panel itself while it is still loading). */
export function focusPanel(panel: HTMLElement | null | undefined) {
  if (!panel) return;
  const heading = panel.querySelector<HTMLElement>('h1');
  if (heading && !heading.hasAttribute('tabindex')) heading.setAttribute('tabindex', '-1');
  (heading || panel).focus();
}

/** Focus the visible tab panel, but only if focus has been lost. */
export function rescueFocus() {
  if (focusLost()) focusPanel(document.querySelector<HTMLElement>('.data-tab-panel:not([hidden])'));
}

/** Rescue after the current event has finished closing things, so the browser's own focus return runs first. */
export const rescueFocusSoon = () => {window.setTimeout(rescueFocus, 0);};
