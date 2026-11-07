import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {FormField} from './form-field';
import {Input} from './input';

afterEach(cleanup);

it('labels the control and describes it with the hint', async () => {
  const {container} = render(<FormField label="Display name" hint="Shown in the model picker">{field => <Input {...field}/>}</FormField>);
  const input = screen.getByLabelText('Display name');
  expect(input).not.toHaveAttribute('aria-invalid');
  expect(input).toHaveAccessibleDescription('Shown in the model picker');
  await expectNoAxeViolations(container);
});

it('marks the control invalid and describes it with the error and the hint', async () => {
  const {container} = render(<FormField label="API address" hint="Include the version path" error="Enter a valid URL">{field => <Input {...field}/>}</FormField>);
  const input = screen.getByLabelText('API address');
  expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(input).toHaveAccessibleDescription('Enter a valid URL Include the version path');
  expect(screen.getByRole('alert')).toHaveTextContent('Enter a valid URL');
  await expectNoAxeViolations(container);
});

it('clears invalid state when the error goes away and keeps the alert mounted', () => {
  const {rerender} = render(<FormField label="Name" error="Required">{field => <Input {...field}/>}</FormField>);
  rerender(<FormField label="Name">{field => <Input {...field}/>}</FormField>);
  expect(screen.getByLabelText('Name')).not.toHaveAttribute('aria-invalid');
  expect(screen.getByRole('alert')).toBeEmptyDOMElement();
});

it('gives each field its own ids and forwards required', () => {
  render(<><FormField label="One" required>{field => <Input {...field}/>}</FormField><FormField label="Two">{field => <Input {...field}/>}</FormField></>);
  const [one, two] = [screen.getByLabelText(/One/), screen.getByLabelText('Two')];
  expect(one).toBeRequired();
  expect(one.id).not.toBe(two.id);
});
