import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {Button} from './button';
import {DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from './dropdown-menu';
import {NavigationLayerContext} from './navigation-layer';

afterEach(cleanup);
function view(onOpen = vi.fn(), onToggle = vi.fn()) {
  return render(<DropdownMenu><DropdownMenuTrigger asChild><Button>More</Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuLabel>Orders</DropdownMenuLabel><DropdownMenuItem onSelect={onOpen}>Open</DropdownMenuItem><DropdownMenuSeparator/><DropdownMenuCheckboxItem checked={false} onCheckedChange={onToggle}>Show row counts</DropdownMenuCheckboxItem><DropdownMenuItem disabled>Combine</DropdownMenuItem></DropdownMenuContent></DropdownMenu>);
}

it('opens from the keyboard, navigates with arrows and activates an item', async () => {
  const user = userEvent.setup(), open = vi.fn();
  view(open);
  await user.tab();
  expect(screen.getByRole('button', {name: 'More'})).toHaveFocus();
  await user.keyboard('{Enter}');
  const menu = await screen.findByRole('menu', {name: 'More'});
  expect(menu).toBeInTheDocument();
  expect(screen.getByRole('menuitem', {name: 'Open'})).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(screen.getByRole('menuitemcheckbox', {name: 'Show row counts'})).toHaveFocus();
  await user.keyboard('{ArrowUp}{Enter}');
  expect(open).toHaveBeenCalledTimes(1);
});

it('closes on Escape and returns focus to the trigger', async () => {
  const user = userEvent.setup();
  view();
  const trigger = screen.getByRole('button', {name: 'More'});
  trigger.focus();
  await user.keyboard('{Enter}');
  await screen.findByRole('menu');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('supports checkbox items and skips disabled items', async () => {
  const user = userEvent.setup(), toggle = vi.fn();
  view(vi.fn(), toggle);
  screen.getByRole('button', {name: 'More'}).focus();
  await user.keyboard('{Enter}');
  await screen.findByRole('menu');
  await user.click(screen.getByRole('menuitemcheckbox', {name: 'Show row counts'}));
  expect(toggle).toHaveBeenCalledWith(true);
});

it('has no axe violations while open', async () => {
  const user = userEvent.setup();
  view();
  screen.getByRole('button', {name: 'More'}).focus();
  await user.keyboard('{Enter}');
  await expectNoAxeViolations(await screen.findByRole('menu'));
});

it('registers its content as a navigation layer', async () => {
  const user = userEvent.setup(), nodes: HTMLElement[] = [];
  render(<NavigationLayerContext.Provider value={node => {nodes.push(node); return () => {};}}><DropdownMenu><DropdownMenuTrigger asChild><Button>More</Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuItem>Open</DropdownMenuItem></DropdownMenuContent></DropdownMenu></NavigationLayerContext.Provider>);
  screen.getByRole('button', {name: 'More'}).focus();
  await user.keyboard('{Enter}');
  await screen.findByRole('menu');
  expect(nodes.some(node => node.getAttribute('role') === 'menu')).toBe(true);
});
