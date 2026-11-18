import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useEffect, useReducer, useRef} from 'react';
import {afterEach, describe, expect, it} from 'vitest';
import {defaultLayout, editorGroups, findTab, newGroup, newTab, type Tab} from '../../lib/layout/model';
import {createState, reduceLayout, type LayoutAction, type LayoutState} from '../../lib/layout/reduce';
import TabGroups from './TabGroups';

afterEach(cleanup);

const mounts: Record<string, number> = {};

/** Stands in for a live browser view or a CLI terminal: it counts mounts per tab and keeps what was typed into it. */
function Probe({tab}: {tab: Tab}) {
  const key = tab.kind + ':' + (tab.ref ?? '');
  const counted = useRef(false);
  useEffect(() => {
    if (!counted.current) {
      counted.current = true;
      mounts[key] = (mounts[key] ?? 0) + 1;
    }
  }, [key]);
  return <textarea aria-label={'input ' + key} defaultValue="" />;
}

const titles: Record<string, string> = {'browser:tab-a': 'Docs', 'browser:tab-b': 'Mail', 'cli:cli-1': 'Claude Code', 'chat:': 'Chat'};
const titleOf = (tab: Tab) => titles[tab.kind + ':' + (tab.ref ?? '')] ?? tab.kind;

function Host({initial, probe}: {initial: LayoutState; probe: {current: LayoutState}}) {
  const [state, dispatch] = useReducer((value: LayoutState, action: LayoutAction) => reduceLayout(value, action), initial);
  probe.current = state;
  return <TabGroups layout={state.book.current} dispatch={dispatch} titleOf={titleOf} render={tab => <Probe tab={tab} />} size={{width: 1000, height: 600}} label="Workspace" />;
}

function setup(tabs: Tab[]) {
  const base = defaultLayout();
  const group = newGroup(tabs);
  const probe = {current: createState({version: 2, current: {...base, root: group, focus: group.id}, named: []})};
  render(<Host initial={probe.current} probe={probe} />);
  return probe;
}

describe('browser and CLI tabs keep their content alive', () => {
  it('names each tab by what it shows and keeps two browser tabs apart', () => {
    setup([newTab('chat'), newTab('browser', 'tab-a'), newTab('browser', 'tab-b'), newTab('cli', 'cli-1')]);
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Chat', 'Docs', 'Mail', 'Claude Code']);
  });

  it('keeps a hidden browser page and a hidden CLI terminal mounted, with what was typed, until the tab closes', async () => {
    Object.keys(mounts).forEach(key => delete mounts[key]);
    const user = userEvent.setup();
    setup([newTab('chat'), newTab('browser', 'tab-a'), newTab('cli', 'cli-1')]);
    await user.click(screen.getByRole('tab', {name: 'Docs'}));
    await user.type(screen.getByLabelText('input browser:tab-a'), 'draft');
    await user.click(screen.getByRole('tab', {name: 'Claude Code'}));
    await user.type(screen.getByLabelText('input cli:cli-1'), 'ls');
    await user.click(screen.getByRole('tab', {name: 'Chat'}));
    expect(screen.getByLabelText('input browser:tab-a', {selector: 'textarea'})).toHaveValue('draft');
    expect(screen.getByLabelText('input cli:cli-1')).toHaveValue('ls');
    expect(screen.getByLabelText('input cli:cli-1').closest('[role=tabpanel]')).toHaveAttribute('hidden');
    expect(mounts).toEqual({'chat:': 1, 'browser:tab-a': 1, 'cli:cli-1': 1});
    await user.click(screen.getByRole('tab', {name: 'Claude Code'}).querySelector('.tab-close')!);
    expect(screen.queryByLabelText('input cli:cli-1')).not.toBeInTheDocument();
    expect(screen.getByLabelText('input browser:tab-a')).toHaveValue('draft');
  });

  it('keeps the same element and one mount when a CLI tab moves to another group', async () => {
    Object.keys(mounts).forEach(key => delete mounts[key]);
    const user = userEvent.setup();
    const probe = setup([newTab('chat'), newTab('cli', 'cli-1')]);
    await user.click(screen.getByRole('tab', {name: 'Claude Code'}));
    const panel = screen.getByLabelText('input cli:cli-1').closest('[role=tabpanel]')!;
    await user.type(screen.getByLabelText('input cli:cli-1'), 'status');
    const tab = editorGroups(probe.current.book.current.root)[0].tabs[1];
    await user.pointer({keys: '[MouseRight]', target: screen.getByRole('tab', {name: 'Claude Code'})});
    await user.click(await screen.findByRole('menuitem', {name: 'Split right'}));
    expect(editorGroups(probe.current.book.current.root)).toHaveLength(2);
    expect(findTab(probe.current.book.current, tab.id)!.tab.id).toBe(tab.id);
    expect(screen.getByLabelText('input cli:cli-1').closest('[role=tabpanel]')).toBe(panel);
    expect(screen.getByLabelText('input cli:cli-1')).toHaveValue('status');
    expect(mounts['cli:cli-1']).toBe(1);
  });
});
