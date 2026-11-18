import type {KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode} from 'react';
import {LIMITS} from '../../lib/layout/model';
import './chrome.css';

type Props = {
  /** The view's name, which is also the side bar's accessible name. */
  title: string;
  /** Pixel width. The side bar is resizable between 200 and 420. */
  width: number;
  /** Header actions (for example the one primary action of the view). */
  actions?: ReactNode;
  children: ReactNode;
  /** Drawers (phone) fill their container and have no resize handle. */
  drawer?: boolean;
  onResizeStart?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onResizeTo?: (width: number) => void;
};

const STEP = 16;

/** The 260px side bar: a header with the view's title, a scrolling body, and a handle on its right edge. */
export default function SideBar({title, width, actions, children, drawer, onResizeStart, onResizeTo}: Props) {
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!onResizeTo) return;
    const step = event.shiftKey ? STEP * 4 : STEP;
    const next =
      event.key === 'ArrowLeft'
        ? width - step
        : event.key === 'ArrowRight'
          ? width + step
          : event.key === 'Home'
            ? LIMITS.sideBar.min
            : event.key === 'End'
              ? LIMITS.sideBar.max
              : event.key === 'Enter'
                ? LIMITS.sideBar.default
                : null;
    if (next === null) return;
    event.preventDefault();
    onResizeTo(Math.max(LIMITS.sideBar.min, Math.min(LIMITS.sideBar.max, next)));
  }
  return (
    <>
      <aside id={drawer ? undefined : 'ide-side-bar'} className="side-bar" data-drawer={drawer || undefined} aria-label={title} style={drawer ? undefined : {width}}>
        <header className="side-bar-header">
          <h2>{title}</h2>
          {actions && <div className="side-bar-actions">{actions}</div>}
        </header>
        <div className="side-bar-body">{children}</div>
      </aside>
      {!drawer && (
        <div
          className="side-bar-resize"
          role="separator"
          aria-label="Resize side bar"
          aria-orientation="vertical"
          aria-valuemin={LIMITS.sideBar.min}
          aria-valuemax={LIMITS.sideBar.max}
          aria-valuenow={width}
          aria-valuetext={width + ' pixels'}
          tabIndex={0}
          onPointerDown={onResizeStart}
          onKeyDown={onKeyDown}
        />
      )}
    </>
  );
}
