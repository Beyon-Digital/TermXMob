import {act, cleanup, render} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import type {ConversationMessageRow} from './conversation-messages';
import type {Session} from './api';
import {useTranscriptScroll} from './use-transcript-scroll';

vi.mock('./api', () => ({request: vi.fn(async () => ({revision: 1})), json: (method: string, value: unknown) => ({method, body: JSON.stringify(value)})}));

const rows = (count: number): ConversationMessageRow[] => Array.from({length: count}, (_, index) => ({id: 'm' + index, role: index % 2 ? 'assistant' : 'user', text: 'message ' + index, task: undefined}));
const session = {id: 'scroll', scroll: 0, turns: []} as unknown as Session;
let away = false, scrollToLatest!: () => void, scrollTo: ReturnType<typeof vi.fn>, hookScrolls = 0;

function Harness({messages}: {messages: ConversationMessageRow[]}) {
  const transcript = useTranscriptScroll({session, messages, onError: vi.fn()});
  away = transcript.awayFromLatest;
  scrollToLatest = transcript.scrollToLatest;
  return <div ref={transcript.scroller} data-testid="scroller"><div style={{height: transcript.virtual.getTotalSize()}}/></div>;
}

const frames = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const place = (element: HTMLElement, top: number) => { element.scrollTop = top; element.dispatchEvent(new Event('scroll')); };

beforeEach(() => {
  vi.useFakeTimers({toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame']});
  hookScrolls = 0;
  // The virtualizer also calls scrollTo to keep its own offset in sync; only count scrolls our hook asked for.
  scrollTo = vi.fn(() => { if (new Error().stack?.includes('use-transcript-scroll.ts')) hookScrolls++; });
  Element.prototype.scrollTo = scrollTo as never;
});
afterEach(() => { cleanup(); vi.useRealTimers(); delete (Element.prototype as {scrollTo?: unknown}).scrollTo; });

function mount(count: number) {
  const view = render(<Harness messages={rows(count)}/>);
  const element = view.getByTestId('scroller');
  Object.defineProperty(element, 'scrollHeight', {configurable: true, value: 5000});
  Object.defineProperty(element, 'clientHeight', {configurable: true, value: 600});
  return {view, element};
}

it('follows new messages while the reader is at the end', async () => {
  const {view, element} = mount(5);
  await frames(500);
  place(element, 4400);
  await frames(50);
  hookScrolls = 0;
  view.rerender(<Harness messages={rows(6)}/>);
  await frames(50);
  expect(hookScrolls).toBeGreaterThan(0);
});

it('never pulls a reader who scrolled up back to the end, and offers a way back', async () => {
  const {view, element} = mount(5);
  await frames(500);
  place(element, 1000);
  await frames(50);
  expect(away).toBe(true);
  hookScrolls = 0;
  view.rerender(<Harness messages={rows(6)}/>);
  view.rerender(<Harness messages={rows(7)}/>);
  await frames(50);
  expect(hookScrolls).toBe(0);
  act(() => scrollToLatest());
  expect(hookScrolls).toBeGreaterThan(0);
  expect(away).toBe(false);
  hookScrolls = 0;
  view.rerender(<Harness messages={rows(8)}/>);
  await frames(50);
  expect(hookScrolls).toBeGreaterThan(0);
});
