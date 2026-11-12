import {cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {Tabs, TabsContent, TabsList, TabsTrigger} from './tabs';

afterEach(cleanup);
const view = () => render(<Tabs defaultValue="a"><TabsList aria-label="Canvas"><TabsTrigger value="a">Overview</TabsTrigger><TabsTrigger value="b">Orders</TabsTrigger><TabsTrigger value="c">Customers</TabsTrigger></TabsList><TabsContent value="a">First panel</TabsContent><TabsContent value="b">Second panel</TabsContent><TabsContent value="c">Third panel</TabsContent></Tabs>);

it('exposes tab roles and shows the selected panel', async () => {
  const {container} = view();
  expect(screen.getByRole('tablist', {name: 'Canvas'})).toBeInTheDocument();
  expect(screen.getByRole('tab', {name: 'Overview'})).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('First panel');
  await expectNoAxeViolations(container);
});

it('moves between tabs with arrows, Home and End', async () => {
  const user = userEvent.setup();
  view();
  await user.tab();
  expect(screen.getByRole('tab', {name: 'Overview'})).toHaveFocus();
  await user.keyboard('{ArrowRight}');
  expect(screen.getByRole('tab', {name: 'Orders'})).toHaveFocus();
  await user.keyboard('{End}');
  expect(screen.getByRole('tab', {name: 'Customers'})).toHaveFocus();
  await user.keyboard('{Home}');
  expect(screen.getByRole('tab', {name: 'Overview'})).toHaveFocus();
});

it('activates a tab with Enter after arrow focus', async () => {
  const user = userEvent.setup();
  view();
  await user.tab();
  await user.keyboard('{ArrowRight}{Enter}');
  expect(screen.getByRole('tab', {name: 'Orders'})).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Second panel');
});

it('uses the shared tab classes so styles come from tokens', () => {
  view();
  expect(screen.getByRole('tablist')).toHaveClass('ui-tabs-list');
  expect(screen.getByRole('tab', {name: 'Overview'})).toHaveClass('ui-tabs-trigger');
});
