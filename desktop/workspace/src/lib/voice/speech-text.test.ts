import {expect, it} from 'vitest';
import {speechText} from './speech-text';

it('drops code blocks and says so, keeps the prose', () => {
  expect(speechText('Run this:\n```bash\nrm -rf build\n```\nThen reload.')).toBe('Run this: Code omitted. Then reload.');
});

it('reads link text, not addresses, and strips markdown marks', () => {
  expect(speechText('See **the [docs](https://example.com/a?b=1)** for `foo()` and _more_.')).toBe(
    'See the docs for foo() and more.',
  );
});

it('turns headings and bullets into plain sentences', () => {
  expect(speechText('# Title\n\n- one\n- two\n\n1. three')).toBe('Title. one. two. three.');
});

it('omits tables and raw html and collapses whitespace', () => {
  expect(speechText('Before\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<div>x</div>   After')).toBe('Before. After.');
});

it('returns an empty string for nothing speakable and bounds long text', () => {
  expect(speechText('   \n```\n```\n')).toBe('Code omitted.');
  expect(speechText('')).toBe('');
  expect(speechText('word '.repeat(5000)).length).toBeLessThanOrEqual(8000);
});
