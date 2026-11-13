import {describe, expect, it} from 'vitest';
import {plainSqlError} from './data-sql-errors';

describe('plainSqlError', () => {
  it.each([
    ['near "FORM": syntax error', /could not be read near "FORM"/],
    ['syntax error at or near "selec"', /near "selec"/],
    ['no such table: ordres', /no table "ordres" in this source/],
    ['relation "ordres" does not exist', /no table "ordres"/],
    ["Table 'shop.ordres' doesn't exist", /no table "shop.ordres"/],
    ['no such column: totl', /no column "totl"/],
    ["Unknown column 'totl' in 'field list'", /no column "totl"/],
    ['ambiguous column name: id', /"id" matches more than one table/],
    ['canceling statement due to statement timeout', /took too long/],
    ['attempt to write a readonly database', /can only read data/],
  ])('rewrites %s', (message, expected) => expect(plainSqlError(message)).toMatch(expected));
  it('keeps messages it does not know and handles empty input', () => {
    expect(plainSqlError('Host unavailable')).toBe('Host unavailable');
    expect(plainSqlError(undefined)).toBe('');
  });
});
