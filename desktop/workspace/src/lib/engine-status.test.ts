import {describe, expect, it} from 'vitest';
import {engineAuthLabel} from './engine-status';

describe('engineAuthLabel', () => {
  it('maps every backend state to a sentence-case label', () => {
    expect(engineAuthLabel('authenticated')).toBe('Signed in');
    expect(engineAuthLabel('unauthenticated')).toBe('Sign-in required');
    expect(engineAuthLabel('expired')).toBe('Sign-in expired');
    expect(engineAuthLabel('not_required')).toBe('No sign-in needed');
  });
  it('never leaks unknown or missing raw values', () => {
    for (const value of ['unknown', undefined, null, '', 'weird_state']) expect(engineAuthLabel(value)).toBe('Sign-in not checked');
  });
});
