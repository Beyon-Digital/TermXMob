import {expect, it} from 'vitest';
import {settingsCategories, settingsSearchText} from './settings-categories';

const find = (words: string) => {
  const terms = words.toLowerCase().split(/\s+/);
  return settingsCategories.filter(row => terms.every(term => settingsSearchText(row).includes(term))).map(row => row.id);
};

it('finds the page for each new setting by the words a person would type', () => {
  expect(find('anthropic')).toEqual(['models']);
  expect(find('claude')).toEqual(['models']);
  expect(find('web search')).toEqual(['connections']);
  expect(find('brave')).toEqual(['connections']);
  expect(find('keep awake')).toEqual(['access']);
  expect(find('terminals')).toEqual(['access']);
  expect(find('heartbeat')).toEqual(['access']);
});

it('keeps a name search narrow', () => {
  expect(find('models')).toEqual(['models']);
  expect(find('no-such-setting')).toEqual([]);
});
