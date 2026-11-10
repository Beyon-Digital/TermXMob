import {expect, it} from 'vitest';
import {stableKey} from './stable-key';

it('gives each object one key that survives reordering and removal of its neighbours', () => {
  const a = {name: 'a'}, b = {name: 'b'}, c = {name: 'a'};
  const keys = [a, b, c].map(stableKey);
  expect(new Set(keys).size).toBe(3);
  expect([c, a].map(stableKey)).toEqual([keys[2], keys[0]]);
  expect(stableKey(a)).toBe(keys[0]);
});
