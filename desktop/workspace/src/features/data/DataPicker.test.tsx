import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataPicker from './DataPicker';
import {expectNoAxeViolations} from '../../test-axe';

const options = [{value: 'a', label: 'Analytics'}, {value: 'f', label: 'Finance'}];
afterEach(cleanup);

it('shows the chosen name on a combobox button and picks with the keyboard', async () => {
  const user = userEvent.setup(), onChange = vi.fn();
  render(<DataPicker label="Data workspace" value="a" options={options} emptyText="No workspaces" onChange={onChange}/>);
  const trigger = screen.getByRole('combobox', {name: 'Data workspace'});
  expect(trigger).toHaveTextContent('Analytics');
  await user.click(trigger);
  expect(await screen.findByRole('option', {name: 'Analytics'})).toHaveAttribute('aria-selected', 'true');
  await user.keyboard('{ArrowDown}{Enter}');
  expect(onChange).toHaveBeenCalledWith('f');
  expect(screen.queryByRole('listbox')).toBeNull();
});
it('closes with Escape and returns focus to the trigger', async () => {
  const user = userEvent.setup();
  render(<DataPicker label="Data source" value="a" options={options} emptyText="No sources yet" onChange={() => undefined}/>);
  await user.click(screen.getByRole('combobox', {name: 'Data source'}));
  await screen.findByRole('listbox');
  await user.keyboard('{Escape}');
  expect(screen.getByRole('combobox', {name: 'Data source'})).toHaveFocus();
});
it('says so when there is nothing to choose and searches long lists', async () => {
  const many = Array.from({length: 12}, (_, index) => ({value: 'v' + index, label: 'Source ' + index}));
  const view = render(<DataPicker label="Data source" value="" options={[]} emptyText="No sources yet" onChange={() => undefined} disabled/>);
  expect(screen.getByRole('combobox', {name: 'Data source'})).toBeDisabled();
  expect(screen.getByRole('combobox', {name: 'Data source'})).toHaveTextContent('No sources yet');
  view.rerender(<DataPicker label="Data source" value="v0" options={many} emptyText="No sources yet" onChange={() => undefined}/>);
  fireEvent.click(screen.getByRole('combobox', {name: 'Data source'}));
  const search = await screen.findByLabelText('Find in data source');
  await userEvent.setup().type(search, '11');
  expect(screen.getAllByRole('option')).toHaveLength(1);
  await expectNoAxeViolations(view.container);
});
