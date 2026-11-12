import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataTabs from './DataTabs';
import {expectNoAxeViolations} from '../../test-axe';
import {initialTabs, openTab, queryTabId, tableTabId, type DataTab, type DataTabsState} from '../../lib/data-tabs';

afterEach(cleanup);
const orders: DataTab = {id: tableTabId('src', 'orders'), kind: 'table', connectionId: 'src', table: 'orders'};
const draft: DataTab = {id: queryTabId(), kind: 'query'};
const title = (tab: DataTab) => tab.kind === 'overview' ? 'Overview' : tab.kind === 'table' ? tab.table! : 'New query';
function view(state: DataTabsState = openTab(openTab(initialTabs(), orders), draft), over: Partial<React.ComponentProps<typeof DataTabs>> = {}) {
  const props = {state, title, onSelect: vi.fn(), onClose: vi.fn(), panel: (tab: DataTab) => <p>{'Panel ' + title(tab)}</p>, ...over};
  return {props, ...render(<DataTabs {...props}/>)};
}

it('renders the overview as a tab and each opened item as another', async () => {
  const {container} = view();
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Overview', 'orders', 'New query']);
  expect(screen.getByRole('tablist', {name: 'Open tables and queries'})).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('never offers to close the overview', () => {
  view(openTab(initialTabs(), orders));
  expect(screen.getByRole('button', {name: 'Close orders'})).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Close Overview'})).not.toBeInTheDocument();
  cleanup();
  view(initialTabs());
  expect(screen.queryByRole('button', {name: /^Close/})).not.toBeInTheDocument();
});

it('selects with arrows, Home and End', async () => {
  const user = userEvent.setup(), {props} = view({...openTab(openTab(initialTabs(), orders), draft), activeId: 'overview'});
  screen.getByRole('tab', {name: 'Overview'}).focus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', {name: 'orders'})).toHaveFocus();
  await user.keyboard('{End}');
  expect(screen.getByRole('tab', {name: 'New query'})).toHaveFocus();
  expect(props.onSelect).toHaveBeenCalledWith(orders.id);
  expect(props.onSelect).toHaveBeenLastCalledWith(draft.id);
});

it('closes the focused closable tab with Delete and by the close button', () => {
  const {props} = view();
  fireEvent.keyDown(screen.getByRole('tab', {name: 'orders'}), {key: 'Delete'});
  expect(props.onClose).toHaveBeenCalledWith(orders.id);
  fireEvent.click(screen.getByRole('button', {name: 'Close New query'}));
  expect(props.onClose).toHaveBeenCalledWith(draft.id);
  fireEvent.keyDown(screen.getByRole('tab', {name: 'Overview'}), {key: 'Delete'});
  expect(props.onClose).toHaveBeenCalledTimes(2);
  fireEvent.click(document.querySelectorAll('.data-tab-close')[0]);
  expect(props.onClose).toHaveBeenLastCalledWith(orders.id);
});

it('keeps a query tab mounted but hidden so its draft survives a tab switch', () => {
  const state = {...openTab(openTab(initialTabs(), orders), draft), activeId: 'overview'};
  view(state, {panel: tab => <p>{'Panel ' + title(tab)}</p>});
  expect(screen.getByText('Panel Overview')).toBeVisible();
  expect(screen.queryByText('Panel orders')).not.toBeInTheDocument();
  const hidden = screen.getByText('Panel New query', {selector: 'p'});
  expect(hidden.closest('[role=tabpanel]')).toHaveAttribute('hidden');
});

it('renders actions and banner around the panels', () => {
  view(initialTabs(), {actions: <button>Copilot</button>, banner: <p role="alert">Boom</p>});
  expect(screen.getByRole('button', {name: 'Copilot'})).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Boom');
});

it('truncates a long title but keeps it in the tooltip and accessible name', () => {
  const long: DataTab = {id: tableTabId('src', 'a_very_long_table_name_for_a_narrow_strip'), kind: 'table', connectionId: 'src', table: 'a_very_long_table_name_for_a_narrow_strip'};
  view(openTab(initialTabs(), long));
  expect(screen.getByRole('tab', {name: 'a_very_long_table_name_for_a_narrow_strip'})).toHaveAttribute('title', 'a_very_long_table_name_for_a_narrow_strip');
});
