import {act, cleanup, renderHook} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {useLayout} from './use-layout';
import {defaultGeometry, layouts} from './docking';

afterEach(cleanup);

it('starts from the chat layout with every panel open', () => {
  const {result} = renderHook(() => useLayout());
  expect(result.current).toMatchObject({layout: 'chat', right: true, bottom: true, sidebarWidth: 244, rightWidth: 380, sidebarHover: false, dock: layouts.chat, dockGeometry: defaultGeometry});
});

it('accepts values and updater functions, like state setters', () => {
  const {result} = renderHook(() => useLayout());
  act(() => { result.current.setLayout('workbench'); result.current.setRightWidth(width => width + 20); result.current.setRightWidth(width => width + 20); });
  expect(result.current.layout).toBe('workbench');
  expect(result.current.rightWidth).toBe(420);
});

it('keeps setters stable across updates', () => {
  const {result} = renderHook(() => useLayout());
  const first = result.current.setLeft;
  act(() => result.current.setSidebarWidth(300));
  expect(result.current.setLeft).toBe(first);
  expect(result.current.sidebarWidth).toBe(300);
});

it('keeps the dock object identity when only another field changes', () => {
  const {result} = renderHook(() => useLayout());
  const dock = result.current.dock;
  act(() => result.current.setBottom(false));
  expect(result.current.dock).toBe(dock);
});
