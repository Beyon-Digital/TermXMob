import {describe, expect, it} from 'vitest';
import {PLACEHOLDER_KINDS, allTabs, editorGroups, findTab, type TabKind} from './model';
import {initialState, reduceLayout, type LayoutAction, type LayoutState} from './reduce';

const run = (state: LayoutState, ...actions: LayoutAction[]) => actions.reduce((value, action) => reduceLayout(value, action), state);
const layoutOf = (state: LayoutState) => state.book.current;
const ofKind = (state: LayoutState, kind: TabKind) => allTabs(layoutOf(state)).filter(tab => tab.kind === kind);

describe('browser tabs', () => {
  it('opens one tab per built-in browser tab and shows an open one again instead of adding a copy', () => {
    const once = run(initialState(), {type: 'openTab', kind: 'browser', ref: 'tab-a'});
    const twice = run(once, {type: 'openTab', kind: 'browser', ref: 'tab-a'});
    expect(ofKind(twice, 'browser')).toHaveLength(1);
    const other = run(twice, {type: 'openTab', kind: 'browser', ref: 'tab-b'});
    expect(ofKind(other, 'browser').map(tab => tab.ref)).toEqual(['tab-a', 'tab-b']);
  });

  it('keeps the whole browser (no ref) apart from a tab that shows one page', () => {
    const whole = run(initialState(), {type: 'openTab', kind: 'browser'});
    const page = run(whole, {type: 'openTab', kind: 'browser', ref: 'tab-a'});
    expect(ofKind(page, 'browser').map(tab => tab.ref)).toEqual([undefined, 'tab-a']);
    // Asking for the Browser with no ref reveals the whole browser, not a page.
    const back = run(page, {type: 'openTab', kind: 'browser'});
    expect(ofKind(back, 'browser')).toHaveLength(2);
    const active = layoutOf(back).root;
    expect(findTab(layoutOf(back), (editorGroups(active)[0].active as string))!.tab.ref).toBeUndefined();
  });

  it('keeps a page tab\'s id, so its live view stays mounted, when it moves to another group', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'browser', ref: 'tab-a'});
    const tab = ofKind(state, 'browser')[0];
    state = run(state, {type: 'splitGroup', group: findTab(layoutOf(state), tab.id)!.group.id, dir: 'row', side: 'after', tab: tab.id});
    expect(findTab(layoutOf(state), tab.id)!.tab.id).toBe(tab.id);
    expect(editorGroups(layoutOf(state).root)).toHaveLength(2);
  });

  it('is not a placeholder: a page does not attach to the whole browser tab', () => {
    expect(PLACEHOLDER_KINDS.has('browser')).toBe(false);
    expect(PLACEHOLDER_KINDS.has('cli')).toBe(false);
  });

  it('reopens a closed page tab with its ref', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'browser', ref: 'tab-a'});
    state = run(state, {type: 'closeTab', tab: ofKind(state, 'browser')[0].id});
    expect(ofKind(state, 'browser')).toHaveLength(0);
    state = run(state, {type: 'reopenTab'});
    expect(ofKind(state, 'browser').map(tab => tab.ref)).toEqual(['tab-a']);
  });
});

describe('CLI tabs', () => {
  it('opens one tab per session and shows an open one again', () => {
    const state = run(
      initialState(),
      {type: 'openTab', kind: 'cli', ref: 'cli-1'},
      {type: 'openTab', kind: 'cli', ref: 'cli-2'},
      {type: 'openTab', kind: 'cli', ref: 'cli-1'},
    );
    expect(ofKind(state, 'cli').map(tab => tab.ref)).toEqual(['cli-1', 'cli-2']);
    expect(layoutOf(state).panel.visible).toBe(false);
  });

  it('goes to the editor area, not the bottom panel, and keeps its id across a move', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'cli', ref: 'cli-1'});
    const tab = ofKind(state, 'cli')[0];
    expect(editorGroups(layoutOf(state).root)[0].tabs.some(item => item.id === tab.id)).toBe(true);
    state = run(state, {type: 'splitGroup', group: findTab(layoutOf(state), tab.id)!.group.id, dir: 'column', side: 'after', tab: tab.id});
    expect(findTab(layoutOf(state), tab.id)!.tab.id).toBe(tab.id);
  });

  it('closing the tab only removes it from the layout', () => {
    let state = run(initialState(), {type: 'openTab', kind: 'cli', ref: 'cli-1'});
    state = run(state, {type: 'closeTab', tab: ofKind(state, 'cli')[0].id});
    expect(ofKind(state, 'cli')).toHaveLength(0);
    state = run(state, {type: 'reopenTab'});
    expect(ofKind(state, 'cli').map(tab => tab.ref)).toEqual(['cli-1']);
  });
});
