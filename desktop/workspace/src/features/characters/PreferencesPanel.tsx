import {Home} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Select} from '../../components/ui/select';
import {SettingsRow, SettingsSection} from '../../components/ui/settings-section';
import {companionHome, type CompanionCapabilities} from '../../lib/companion-native';
import type {CompanionPreferences} from '../../lib/characters';
import type {SavePreferences} from './use-character-library';
import {ActionError, useAction} from './use-action';

const sizes = [
  {value: 64, label: 'Small'},
  {value: 96, label: 'Medium'},
  {value: 128, label: 'Large'},
  {value: 160, label: 'Extra large'},
];

interface Props {
  preferences: CompanionPreferences;
  capabilities: CompanionCapabilities | null;
  save: SavePreferences;
}

export function PreferencesPanel({preferences, capabilities, save}: Props) {
  const action = useAction();
  const change = (build: Parameters<SavePreferences>[0]) => void action.run(() => save(build));
  return (
    <SettingsSection
      level={3}
      title="Preferences"
      description="Saved for your account on this host. Four characters explore at a time; the others stay in the home dock."
    >
      <SettingsRow
        label="Let characters explore"
        description={
          capabilities?.desktop
            ? 'Characters walk around your desktop, even while TermX is hidden.'
            : 'Characters have a home inside your workspace.'
        }
      >
        {control => (
          <input
            {...control}
            type="checkbox"
            checked={preferences.enabled}
            onChange={() => change(current => ({enabled: !current.enabled}))}
          />
        )}
      </SettingsRow>
      <SettingsRow label="Size">
        {control => (
          <Select
            {...control}
            value={preferences.size}
            onChange={event => {
              const size = Number(event.target.value);
              change(() => ({size}));
            }}
          >
            {sizes.map(row => (
              <option value={row.value} key={row.value}>
                {row.label}
              </option>
            ))}
          </Select>
        )}
      </SettingsRow>
      <SettingsRow label="Activity">
        {control => (
          <Select
            {...control}
            value={preferences.activity}
            onChange={event => {
              const activity = event.target.value === 'playful' ? 'playful' : 'calm';
              change(() => ({activity}));
            }}
          >
            <option value="calm">Calm</option>
            <option value="playful">Playful</option>
          </Select>
        )}
      </SettingsRow>
      <SettingsRow label="Reduced motion" description="Characters hold still instead of walking and animating.">
        {control => (
          <input
            {...control}
            type="checkbox"
            checked={preferences.reduced_motion}
            onChange={() => change(current => ({reduced_motion: !current.reduced_motion}))}
          />
        )}
      </SettingsRow>
      <SettingsRow label="Characters at home" description={capabilities?.reason}>
        <Button variant="outline" onClick={() => void action.run(() => companionHome())}>
          <Home size={14} />
          Bring pets home
        </Button>
      </SettingsRow>
      {action.error && (
        <li className="settings-row">
          <ActionError action={action} />
        </li>
      )}
    </SettingsSection>
  );
}
