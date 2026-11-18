import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useReducer, useState} from 'react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {defaultLayout, newGroup, newTab, type Layout, type TabKind} from '../../lib/layout/model';
import {createState, reduceLayout, type LayoutAction, type LayoutState} from '../../lib/layout/reduce';
import TabGroups from './TabGroups';
import {KIND_LABEL, OPENABLE_KINDS, PANEL_OPENABLE_KINDS} from './tab-meta';

afterEach(cleanup);

function Host({layout, onNewTab, phone, hideStrip, chosenTab}: {layout: Layout; onNewTab?: (kind: TabKind, group: string | undefined) => boolean; phone?: boolean; hideStrip?: boolean; chosenTab?: string | null}) {
  const [state, dispatch] = useReducer((value: LayoutState, action: LayoutAction) => reduceLayout(value, action), createState({version: 2, current: layout, named: []}));
  return (
    <TabGroups
      layout={state.book.current}
      dispatch={dispatch}
      size={{width: 1000, height: 600}}
      phone={phone}
      hideStrip={hideStrip}
      chosenTab={chosenTab}
      titleOf={tab => KIND_LABEL[tab.kind] + (tab.ref ? ' ' + tab.ref : '')}
      openableFor={group => (group.id === state.book.current.panel.group.id ? PANEL_OPENABLE_KINDS : OPENABLE_KINDS)}
      onNewTab={onNewTab}
      render={tab => <p>{KIND_LABEL[tab.kind]} content</p>}
    />
  );
}

const base = (): Layout => {
  const layout = defaultLayout();
  return {...layout, panel: {...layout.panel, visible: true}};
};

describe('TabGroups host hooks', () => {
  it('offers each group its own kinds in the + menu: the panel\'s differ from the editor area\'s', async () => {
    const user = userEvent.setup();
    render(<Host layout={base()} />);
    const [editor, panel] = screen.getAllByRole('group');
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    expect((await screen.findAllByRole('menuitemcheckbox')).map(item => item.textContent)).toEqual(PANEL_OPENABLE_KINDS.map(kind => KIND_LABEL[kind]));
    await user.keyboard('{Escape}');
    await user.click(within(editor).getByRole('button', {name: 'New tab'}));
    expect((await screen.findAllByRole('menuitemcheckbox')).map(item => item.textContent)).toEqual(OPENABLE_KINDS.map(kind => KIND_LABEL[kind]));
  });

  it('lets the host handle a new tab, and opens the kind itself when the host does not', async () => {
    const user = userEvent.setup();
    const handled = vi.fn((kind: TabKind) => kind === 'terminal');
    render(<Host layout={base()} onNewTab={handled} />);
    const panel = screen.getAllByRole('group')[1];
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Terminal'}));
    expect(handled).toHaveBeenCalledWith('terminal', expect.any(String));
    expect(within(panel).getAllByRole('tab')).toHaveLength(1);
    await user.click(within(panel).getByRole('button', {name: 'New tab'}));
    await user.click(await screen.findByRole('menuitemcheckbox', {name: 'Problems'}));
    expect(within(panel).getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Terminal', 'Problems']);
  });

  it('draws no tab strip on a phone when the host provides its own, and shows the tab the host chose', () => {
    const layout = defaultLayout();
    const group = newGroup([newTab('chat'), newTab('browser')]);
    const next = {...layout, root: group, focus: group.id};
    const {rerender} = render(<Host layout={next} phone hideStrip chosenTab={next.root && (next.root as {tabs: {id: string}[]}).tabs[1].id} />);
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByText('Browser content')).toBeVisible();
    rerender(<Host layout={next} phone chosenTab={undefined} />);
    expect(screen.getByRole('tablist')).toBeInTheDocument();
  });

  it('keeps a controlled tab choice out of the layout', () => {
    const layout = defaultLayout();
    const group = newGroup([newTab('chat'), newTab('browser')]);
    const next = {...layout, root: group, focus: group.id};
    function Controlled() {
      const [chosen, setChosen] = useState<string | null>(null);
      return (
        <>
          <button onClick={() => setChosen(group.tabs[1].id)}>choose</button>
          <TabGroups layout={next} dispatch={() => {}} size={{width: 400, height: 600}} phone hideStrip chosenTab={chosen} onChosenTab={setChosen} titleOf={tab => KIND_LABEL[tab.kind]} render={tab => <p>{KIND_LABEL[tab.kind]} content</p>} />
        </>
      );
    }
    render(<Controlled />);
    return userEvent.click(screen.getByRole('button', {name: 'choose'})).then(() => {
      expect(screen.getByText('Browser content')).toBeVisible();
    });
  });
});
