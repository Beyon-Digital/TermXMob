/**
 * Two small buses between source control and the rest of the shell.
 *
 * - "Changed": something in the checkout moved (an operation finished, the changed-file list changed). The branch status hook,
 *   the Source control view and open diff tabs listen so they refresh now instead of at the next poll.
 * - Commands: the command palette and keys ask the Source control view to commit or stage everything, and ask the focused diff
 *   tab to move to the next or previous change. The view may not be mounted when the command runs, so its command waits until
 *   the view takes it.
 */

const CHANGED = 'termx-scm-changed';

export function notifyScmChanged() {
  window.dispatchEvent(new Event(CHANGED));
}

export function onScmChanged(listener: () => void) {
  window.addEventListener(CHANGED, listener);
  return () => window.removeEventListener(CHANGED, listener);
}

export type ScmCommand = 'commit' | 'stageAll' | 'refresh';

export const SCM_COMMAND_EVENT = 'termx-scm-command';

let pending: ScmCommand | null = null;

export function requestScmCommand(command: ScmCommand) {
  pending = command;
  window.dispatchEvent(new Event(SCM_COMMAND_EVENT));
}

/** Take the command that is waiting for the view, if there is one. */
export function takeScmCommand(): ScmCommand | null {
  const command = pending;
  pending = null;
  return command;
}

export type ChangeStep = 'next' | 'previous';

export const DIFF_STEP_EVENT = 'termx-diff-step';

/** What a diff tab receives: the direction, and whether a tab has already taken it (only one tab moves). */
export type DiffStepDetail = {step: ChangeStep; claimed: boolean};

export function requestChangeStep(step: ChangeStep) {
  window.dispatchEvent(new CustomEvent<DiffStepDetail>(DIFF_STEP_EVENT, {detail: {step, claimed: false}}));
}
