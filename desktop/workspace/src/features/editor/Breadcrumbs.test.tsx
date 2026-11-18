import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import Breadcrumbs from './Breadcrumbs';
import {expectNoAxeViolations} from '../../test-axe';

afterEach(cleanup);

it('renders one segment per path part and marks the file as current', () => {
  render(<Breadcrumbs path="src/features/editor/language.ts" />);
  const nav = screen.getByRole('navigation', {name: 'File path'});
  const items = within(nav).getAllByRole('listitem');
  expect(items.map((item) => item.textContent)).toEqual(['src', 'features', 'editor', 'language.ts']);
  expect(items.at(-1)).toHaveAttribute('aria-current', 'page');
  expect(items[0]).not.toHaveAttribute('aria-current');
});

it('tolerates leading and doubled separators and a single-segment path', () => {
  const view = render(<Breadcrumbs path="/a//b.ts" />);
  expect(screen.getAllByRole('listitem').map((item) => item.textContent)).toEqual(['a', 'b.ts']);
  view.rerender(<Breadcrumbs path="README.md" />);
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
});

it('says when a file is read only and has no axe violations', async () => {
  const {container} = render(<Breadcrumbs path="src/a.ts" readOnly />);
  expect(screen.getByText('Read only')).toBeVisible();
  await expectNoAxeViolations(container);
});
