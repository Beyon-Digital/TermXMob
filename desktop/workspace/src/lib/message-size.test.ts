import {describe, expect, it} from 'vitest';
import {estimateMessageHeight} from './message-size';

describe('estimateMessageHeight', () => {
  it('falls back to a default for a missing row', () => {
    expect(estimateMessageHeight(undefined)).toBe(140);
  });

  it('grows with the text, with a floor and a ceiling', () => {
    const short = estimateMessageHeight({role: 'assistant', text: 'ok'});
    const long = estimateMessageHeight({role: 'assistant', text: 'word '.repeat(400)});
    const huge = estimateMessageHeight({role: 'assistant', text: 'word '.repeat(40000)});
    expect(short).toBeGreaterThan(40);
    expect(long).toBeGreaterThan(short);
    expect(huge).toBeLessThanOrEqual(1700);
  });

  it('counts explicit line breaks and reserves room for a task activity row', () => {
    const flat = estimateMessageHeight({role: 'assistant', text: 'a'.repeat(30)});
    const lines = estimateMessageHeight({role: 'assistant', text: 'a\n'.repeat(15)});
    expect(lines).toBeGreaterThan(flat);
    expect(estimateMessageHeight({role: 'assistant', text: '', task: {}})).toBeGreaterThan(estimateMessageHeight({role: 'assistant', text: ''}));
  });

  it('keeps long user messages shorter than assistant ones because they are capped', () => {
    expect(estimateMessageHeight({role: 'user', text: 'x'.repeat(100000)})).toBeLessThan(estimateMessageHeight({role: 'assistant', text: 'x'.repeat(100000)}));
  });
});
