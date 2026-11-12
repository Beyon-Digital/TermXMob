import {useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode} from 'react';

export const COPILOT_MIN = 320, COPILOT_MAX = 720, COPILOT_DEFAULT = 400, COPILOT_STEP = 16;
const KEY = 'termx.data.copilot.width';
const clamp = (value: number) => Math.min(COPILOT_MAX, Math.max(COPILOT_MIN, Math.round(value)));
function readWidth() {
  try {const value = Number(window.localStorage.getItem(KEY)); return value ? clamp(value) : COPILOT_DEFAULT;} catch {return COPILOT_DEFAULT;}
}

/** The right-hand Copilot slot. A separator on its left edge resizes it with the pointer or the arrow keys. */
export default function CopilotPanel({children}: {children: ReactNode}) {
  const [width, setWidth] = useState(readWidth), drag = useRef<{x: number; width: number} | null>(null);
  const remember = useCallback((next: number) => {try {window.localStorage.setItem(KEY, String(next));} catch {/* The width is a convenience only. */}}, []);
  const set = useCallback((next: number) => {const value = clamp(next); setWidth(value); remember(value);}, [remember]);
  useEffect(() => () => {drag.current = null;}, []);
  function key(event: KeyboardEvent<HTMLDivElement>) {
    // The panel sits on the right, so Left grows it and Right shrinks it.
    const step = event.shiftKey ? COPILOT_STEP * 4 : COPILOT_STEP;
    if (event.key === 'ArrowLeft') set(width + step);
    else if (event.key === 'ArrowRight') set(width - step);
    else if (event.key === 'Home') set(COPILOT_MIN);
    else if (event.key === 'End') set(COPILOT_MAX);
    else if (event.key === 'Enter') set(COPILOT_DEFAULT);
    else return;
    event.preventDefault();
  }
  function down(event: PointerEvent<HTMLDivElement>) {
    drag.current = {x: event.clientX, width};
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  const move = (event: PointerEvent<HTMLDivElement>) => {if (drag.current) setWidth(clamp(drag.current.width + drag.current.x - event.clientX));};
  const up = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const next = clamp(drag.current.width + drag.current.x - event.clientX);
    drag.current = null; set(next);
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };
  return <aside className="data-copilot-panel" aria-label="Data Copilot" style={{'--copilot-width': width + 'px'} as CSSProperties}>
    <div className="copilot-splitter" role="separator" aria-orientation="vertical" aria-label="Resize Copilot" aria-valuemin={COPILOT_MIN} aria-valuemax={COPILOT_MAX} aria-valuenow={width} aria-valuetext={width + ' pixels wide'}
      tabIndex={0} onKeyDown={key} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onDoubleClick={() => set(COPILOT_DEFAULT)}/>
    {children}
  </aside>;
}
