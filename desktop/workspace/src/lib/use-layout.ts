import {useMemo, useReducer, type Dispatch, type SetStateAction} from 'react';
import {defaultGeometry, layouts, type DockGeometry, type DockMap} from './docking';

export type Layout = 'chat' | 'workbench' | 'browser' | 'computer' | 'artifacts' | 'review' | 'terminal' | 'focus';

/** Panel arrangement of the workspace shell. Auth, sessions and navigation state live elsewhere. */
export interface LayoutState {
  layout: Layout;
  dock: DockMap;
  dockGeometry: DockGeometry;
  left: boolean;
  right: boolean;
  bottom: boolean;
  sidebarWidth: number;
  rightWidth: number;
  sidebarHover: boolean;
}

type Action = {[K in keyof LayoutState]: {key: K; value: SetStateAction<LayoutState[K]>}}[keyof LayoutState];

const initial = (): LayoutState => ({
  layout: 'chat', dock: layouts.chat, dockGeometry: defaultGeometry, left: innerWidth > 850, right: true, bottom: true,
  sidebarWidth: 244, rightWidth: 380, sidebarHover: false,
});

function reducer(state: LayoutState, action: Action): LayoutState {
  const previous = state[action.key];
  const next = typeof action.value === 'function' ? (action.value as (value: typeof previous) => typeof previous)(previous) : action.value;
  return Object.is(previous, next) ? state : {...state, [action.key]: next};
}

/** One reducer for the shell's panel state. Setters keep the `useState` signature and never change identity. */
export function useLayout() {
  const [state, dispatch] = useReducer(reducer, undefined, initial);
  const setters = useMemo(() => {
    const setter = <K extends keyof LayoutState>(key: K): Dispatch<SetStateAction<LayoutState[K]>> => value => dispatch({key, value} as Action);
    return {
      setLayout: setter('layout'), setDock: setter('dock'), setDockGeometry: setter('dockGeometry'), setLeft: setter('left'),
      setRight: setter('right'), setBottom: setter('bottom'), setSidebarWidth: setter('sidebarWidth'),
      setRightWidth: setter('rightWidth'), setSidebarHover: setter('sidebarHover'),
    };
  }, []);
  return {...state, ...setters};
}
