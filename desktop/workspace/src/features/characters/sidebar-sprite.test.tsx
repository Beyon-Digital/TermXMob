import {act, cleanup, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it} from 'vitest';
import {SIDEBAR_SPRITE_KEY, readSidebarSprite, useSidebarSprite, writeSidebarSprite} from '../../lib/sidebar-sprite';
import {expectNoAxeViolations} from '../../test-axe';
import {PreferencesPanel} from './PreferencesPanel';

beforeEach(() => {
  localStorage.clear();
});
afterEach(cleanup);

const preferences = {enabled: false, size: 96, activity: 'calm', reduced_motion: false} as never;

function Probe() {
  return <output aria-label="sprite">{String(useSidebarSprite())}</output>;
}

describe('the character beside Main agent', () => {
  it('is off by default and stored per device', () => {
    expect(readSidebarSprite()).toBe(false);
    writeSidebarSprite(true);
    expect(localStorage.getItem(SIDEBAR_SPRITE_KEY)).toBe('on');
    writeSidebarSprite(false);
    expect(localStorage.getItem(SIDEBAR_SPRITE_KEY)).toBeNull();
  });

  it('is a switch in Settings > Characters that the Agents view follows at once', async () => {
    const user = userEvent.setup();
    const {container} = render(
      <>
        <PreferencesPanel preferences={preferences} capabilities={null} save={(async () => {}) as never} />
        <Probe />
      </>,
    );
    const toggle = screen.getByRole('checkbox', {name: 'Show a character beside Main agent'});
    expect(toggle).not.toBeChecked();
    expect(screen.getByLabelText('sprite')).toHaveTextContent('false');
    await user.click(toggle);
    expect(toggle).toBeChecked();
    expect(screen.getByLabelText('sprite')).toHaveTextContent('true');
    expect(readSidebarSprite()).toBe(true);
    act(() => writeSidebarSprite(false));
    expect(toggle).not.toBeChecked();
    await expectNoAxeViolations(container);
  });
});
