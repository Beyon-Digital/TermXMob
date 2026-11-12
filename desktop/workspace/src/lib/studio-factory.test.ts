import {describe, expect, it} from 'vitest';
import {buildFactoryScenes, factoryRatio} from './studio-factory';

describe('source content-factory scene drafts', () => {
  it('builds distinct editable shot directions without a provider request', () => {
    const scenes = buildFactoryScenes('A handmade ceramic collection', 'product', 6);
    expect(scenes).toHaveLength(6);
    expect(new Set(scenes.map(row => row.prompt)).size).toBe(6);
    expect(scenes.every(row => row.prompt.includes('A handmade ceramic collection') && row.prompt.includes('crisp product detail'))).toBe(true);
  });
  it.each([0, 7, 1.5, Number.NaN])('rejects an unbounded or invalid scene count %s', count => {
    expect(() => buildFactoryScenes('A brief', 'cinematic', count)).toThrow();
  });
  it('rejects blank and oversized briefs', () => {
    expect(() => buildFactoryScenes(' ', 'cinematic', 3)).toThrow();
    expect(() => buildFactoryScenes('a'.repeat(10001), 'cinematic', 3)).toThrow();
  });
  it('reports when a platform ratio must fall back to a supported output', () => {
    expect(factoryRatio('vertical', ['16:9'])).toEqual({ratio: '16:9', exact: false});
    expect(factoryRatio('vertical', ['16:9', '9:16'])).toEqual({ratio: '9:16', exact: true});
    expect(factoryRatio('wide', [])).toEqual({ratio: '', exact: false});
  });
});
