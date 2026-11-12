import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {Button} from './button';
import {NavigationLayerContext} from './navigation-layer';
import {Popover, PopoverContent, PopoverTrigger} from './popover';

afterEach(cleanup);
const view = () => render(<Popover><PopoverTrigger asChild><Button>Filter</Button></PopoverTrigger><PopoverContent aria-label="Filter options"><label>Value<input/></label><Button>Apply</Button></PopoverContent></Popover>);

it('opens on click, moves focus inside and passes axe', async () => {
  const user = userEvent.setup();
  view();
  await user.click(screen.getByRole('button', {name: 'Filter'}));
  const dialog = await screen.findByRole('dialog', {name: 'Filter options'});
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  await expectNoAxeViolations(dialog);
});

it('closes on Escape and returns focus to the trigger', async () => {
  const user = userEvent.setup();
  view();
  const trigger = screen.getByRole('button', {name: 'Filter'});
  await user.click(trigger);
  await screen.findByRole('dialog');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('registers its content as a navigation layer so a floating sidebar stays open', async () => {
  const user = userEvent.setup();
  const nodes: HTMLElement[] = [], unregister = vi.fn();
  const register = (node: HTMLElement) => {nodes.push(node); return unregister;};
  render(<NavigationLayerContext.Provider value={register}><Popover><PopoverTrigger asChild><Button>Filter</Button></PopoverTrigger><PopoverContent aria-label="Filter options">Body</PopoverContent></Popover></NavigationLayerContext.Provider>);
  await user.click(screen.getByRole('button', {name: 'Filter'}));
  await screen.findByRole('dialog');
  expect(nodes.some(node => node.getAttribute('role') === 'dialog' && node.dataset.state === 'open')).toBe(true);
});
