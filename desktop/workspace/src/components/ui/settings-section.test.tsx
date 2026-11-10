import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {SettingsRow, SettingsSection} from './settings-section';

afterEach(cleanup);

it('names the section by its heading and groups rows', async () => {
  const {container} = render(
    <SettingsSection title="Appearance" description="How the workspace looks">
      <SettingsRow label="Theme" description="Follows the system by default">{props => <select {...props}><option>System</option></select>}</SettingsRow>
      <SettingsRow label="Density">{props => <input type="checkbox" {...props}/>}</SettingsRow>
    </SettingsSection>);
  const section = screen.getByRole('region', {name: 'Appearance'});
  expect(within(section).getByRole('heading', {name: 'Appearance', level: 2})).toBeInTheDocument();
  expect(within(section).getByText('How the workspace looks')).toBeInTheDocument();
  expect(within(section).getAllByRole('listitem')).toHaveLength(2);
  await expectNoAxeViolations(container);
});

it('links a row label and description to its control', () => {
  render(<SettingsSection title="Models"><SettingsRow label="Default model" description="Used for new chats">{props => <input {...props}/>}</SettingsRow></SettingsSection>);
  const control = screen.getByLabelText('Default model');
  expect(control).toHaveAccessibleDescription('Used for new chats');
});

it('accepts plain children for rows that hold their own labelled controls', async () => {
  const {container} = render(<SettingsSection title="Access"><SettingsRow label="Session"><button type="button">Revoke</button></SettingsRow></SettingsSection>);
  expect(screen.getByRole('button', {name: 'Revoke'})).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('titles a section with an h3 when the page already owns the h2', () => {
  render(<SettingsSection title="Account" level={3}><SettingsRow label="Name"><span>Alex</span></SettingsRow></SettingsSection>);
  expect(screen.getByRole('heading', {name: 'Account', level: 3})).toBeInTheDocument();
});
