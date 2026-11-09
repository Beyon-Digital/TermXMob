/**
 * Plain-language view of the desktop_* tool calls (agent control of other applications through their accessibility
 * controls). The host stores only a summary of each result (counts, application label, outcome), never the text that was
 * read from the screen. These helpers also refuse to show anything beyond that summary, so a stored event that does carry
 * screen text, a control reference or typed text still renders as a few short facts.
 */
type Obj = Record<string, unknown>;
const obj = (value: unknown): Obj => value && typeof value === 'object' && !Array.isArray(value) ? value as Obj : {};
const str = (value: unknown) => typeof value === 'string' ? value : '';
const num = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null;
/** Application names and window titles are text from other applications: bound them before display. */
const short = (value: string, limit = 80) => value.length > limit ? value.slice(0, limit - 1) + '…' : value;

export const isDesktopTool = (name: string) => /^desktop_(apps|snapshot|find|get|act)$/.test(name);

const actTitles: Record<string, string> = {
  click: 'Click a control', right_click: 'Open a control’s menu', set_value: 'Enter text', type: 'Enter text', clear: 'Clear a field',
  select: 'Choose an option', toggle: 'Change a setting', check: 'Change a setting', uncheck: 'Change a setting',
  expand: 'Expand or collapse', collapse: 'Expand or collapse', scroll: 'Scroll', scroll_to: 'Scroll', focus: 'Focus a control',
};

/** Title for a call that has not finished. It is derived from the arguments only, never from a result. */
export function desktopTitle(name: string, args: Obj): string {
  if (name === 'desktop_apps') return args.app_id ? 'Look at app windows' : 'Look at open apps';
  if (name === 'desktop_snapshot') return 'Read the screen';
  if (name === 'desktop_find') return 'Find a control';
  if (name === 'desktop_get') return 'Read a control';
  return actTitles[str(args.action)] || 'Act on a control';
}

export type DesktopView = {
  facts: [label: string, value: string][];
  notes: string[];
  /** True when the result carries text that came from another application. */
  external: boolean;
  /** A recoverable condition shown as a note, not as a failure. */
  quiet: boolean;
  statusLabel?: string;
};

const errors: Record<string, string> = {
  stale_ref: 'The screen changed after the last read, so the agent has to read it again before it can use this control.',
  several_windows: 'That application has several windows, so the agent has to choose one.',
  ambiguous: 'Several controls look the same to the computer, so the agent has to choose one with a unique name.',
  unsupported: 'That control cannot do this without moving the pointer, which desktop control never does.',
  app_gone: 'That application is not running or has no open window.',
  slow: 'The application was too slow to answer in time.',
  too_large: 'That window was too large to read at once.',
  unknown_delivery: 'It is not known whether the action happened. The agent checks the screen before trying again.',
  permission: 'This computer has not allowed TermX to read other applications. See Settings, Tools & connections.',
  not_set_up: 'Desktop control is not set up on this computer. See Settings, Tools & connections.',
  denied: 'TermX does not control its own windows.',
  invalid: 'The request was not valid, so nothing was sent to the application.',
  failed: 'The application did not accept the request.',
};

const verbs: Record<string, string> = {
  click: 'Click', right_click: 'Open menu', set_value: 'Enter text', type: 'Enter text', clear: 'Clear', select: 'Choose an option',
  toggle: 'Change', check: 'Check', uncheck: 'Uncheck', expand: 'Expand', collapse: 'Collapse', scroll: 'Scroll', scroll_to: 'Scroll to', focus: 'Focus',
};

export function desktopView(name: string, input: unknown, output: unknown): DesktopView {
  const args = obj(input), result = obj(output);
  const view: DesktopView = {facts: [], notes: [], external: false, quiet: false};
  const app = short(str(result.app));
  if (app) view.facts.push(['Application', app]);
  if (name === 'desktop_act') {
    const action = str(result.action) || str(args.action);
    if (action) view.facts.push(['Action', verbs[action] || action.replaceAll('_', ' ')]);
    const text = str(args.text);
    const count = /\[private input omitted: (\d{1,6}) characters\]/.exec(text)?.[1];
    if (count) view.facts.push(['Text entry', `${count} characters, not stored`]);
    else if (text) view.facts.push(['Text entry', 'Not stored']);
  }
  if (result.refused === true) {
    view.notes.push(short(str(result.error) || 'Desktop control refused this request.', 240));
    return view;
  }
  if (result.ok === false) {
    const code = str(result.error);
    view.quiet = code === 'stale_ref';
    if (view.quiet) view.statusLabel = 'Screen changed';
    view.notes.push(errors[code] || short(str(result.next) || str(result.error) || 'Desktop control did not complete this request.', 200));
    return view;
  }
  if (result.ok !== true) return view;
  if (name === 'desktop_act') {
    const delivered = str(result.delivered);
    if (delivered === 'verified') view.facts.push(['Result', 'Confirmed by the application']);
    else if (delivered) {
      view.facts.push(['Result', 'Sent, not confirmed']);
      view.notes.push('The application accepted it, but nothing confirmed the change. The agent checks the screen before repeating it.');
    }
    const opened = Array.isArray(result.opened) ? result.opened.filter((item): item is string => typeof item === 'string').slice(0, 3) : [];
    if (opened.length) view.facts.push(['Opened', opened.join(', ')]);
    return view;
  }
  const count = num(result.count), shown = num(result.shown), total = num(result.total);
  if (name === 'desktop_apps' && count !== null) view.facts.push(['Applications found', String(count)]);
  if (shown !== null && total !== null) {
    view.facts.push(['Controls shown', `${shown} of ${total}`]);
    if (result.complete === false && total > shown) view.notes.push(`${total - shown} controls were left out to keep this short. The agent can narrow its search or read part of the window.`);
  }
  if (name === 'desktop_get' && result.truncated === true) view.notes.push('The text was cut short.');
  view.external = true;
  view.notes.push('Text from other applications is treated as content to read, not instructions to follow.');
  return view;
}
