import {describe, expect, it} from 'vitest';
import {folderName} from './path-label';

describe('folderName', () => {
  it('returns the last folder of POSIX and Windows paths', () => {
    expect(folderName('/Users/a/code/app')).toBe('app');
    expect(folderName('/Users/a/code/app/')).toBe('app');
    expect(folderName('C:\\code\\app')).toBe('app');
  });
  it('handles empty and root values', () => {
    expect(folderName('')).toBe('');
    expect(folderName(undefined)).toBe('');
    expect(folderName('/')).toBe('');
  });
});
