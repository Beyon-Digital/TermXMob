import {afterEach, describe, expect, it} from 'vitest';
import {SAVE_FILE_EVENT, answersSaveRequest, requestSaveFile} from './save-request';

afterEach(() => {
  document.body.innerHTML = '';
});

function tabs() {
  document.body.innerHTML =
    '<section class="file-tab" id="a"><textarea id="a-text"></textarea></section><section class="file-tab" id="b"></section><input id="chat">';
  return {
    a: document.getElementById('a')!,
    b: document.getElementById('b')!,
    inside: document.getElementById('a-text')!,
    outside: document.getElementById('chat')!,
  };
}

describe('save requests', () => {
  it('are broadcast as one window event', () => {
    let seen = 0;
    const count = () => seen++;
    window.addEventListener(SAVE_FILE_EVENT, count);
    requestSaveFile();
    window.removeEventListener(SAVE_FILE_EVENT, count);
    expect(seen).toBe(1);
  });

  it('are answered by the tab that holds focus', () => {
    const {a, b, inside} = tabs();
    expect(answersSaveRequest(a, true, inside)).toBe(true);
    expect(answersSaveRequest(b, true, inside)).toBe(false);
  });

  it('are answered by every visible tab when focus is elsewhere, and by no hidden tab', () => {
    const {a, b, outside} = tabs();
    expect(answersSaveRequest(a, true, outside)).toBe(true);
    expect(answersSaveRequest(b, true, outside)).toBe(true);
    expect(answersSaveRequest(a, false, outside)).toBe(false);
    expect(answersSaveRequest(null, true, outside)).toBe(false);
  });
});
