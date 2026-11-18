import {useCallback, useRef, type Dispatch, type PointerEvent as ReactPointerEvent, type SetStateAction} from 'react';

type Options = {
  width: number;
  setWidth: Dispatch<SetStateAction<number>>;
  /** Called once when the drag starts, so the layout can remember itself for Undo. */
  onStart: () => void;
  min?: number;
  max?: number;
};

/** Drag the side bar's right edge. The width is clamped to the side bar's limits (200 to 420). */
export function useSidebarResize({width, setWidth, onStart, min = 200, max = 420}: Options) {
  const latest = useRef({width, setWidth, onStart, min, max});
  latest.current = {width, setWidth, onStart, min, max};
  return useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const {width: origin, onStart: start} = latest.current;
    start();
    event.currentTarget.setPointerCapture(event.pointerId);
    const from = event.clientX;
    const move = (pointer: PointerEvent) => {
      const {setWidth: set, min: low, max: high} = latest.current;
      set(Math.max(low, Math.min(high, origin + (pointer.clientX - from))));
    };
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
  }, []);
}
