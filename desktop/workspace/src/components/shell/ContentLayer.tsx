import {useRef, type ReactNode} from 'react';
import type {Tab, TabKind} from '../../lib/layout/model';
import type {Placement} from '../../lib/layout/rects';
import './layout.css';

/**
 * The content of every open tab, in one flat list.
 *
 * A tab's content is a child of this layer, never of its group. Moving a tab to another group, splitting or maximizing only
 * changes the rectangle in `style`, so React keeps the same element and the editor, terminal or browser stream inside it
 * stays mounted. Two rules make that hold:
 * - the children are keyed by tab id and kept in the order they were first shown, so a move never reorders DOM nodes
 *   (reordering would reload an iframe);
 * - a tab's content is created when the tab is first on screen and removed only when the tab closes. Until then, and
 *   whenever it is not the active tab of an on-screen group, it is `hidden` and `inert`.
 */

export type TabRender = (tab: Tab, state: {visible: boolean}) => ReactNode;

/**
 * Legacy panel class names (`dock-conversation`, `dock-editor`...) that existing panel CSS and focus code look for. The old
 * `dock-pane` class is left off on purpose: legacy rules hide `.dock-pane` by matching its inline `style` for `left`.
 */
const PANEL_CLASS: Partial<Record<TabKind, string>> = {chat: 'conversation', file: 'editor', diff: 'review'};
export const panelClass = (kind: TabKind) => PANEL_CLASS[kind] ?? kind;

type Props = {
  placements: Placement[];
  render: TabRender;
  /** Pointer or focus landed inside a tab's content: its group takes the focus. */
  onFocusGroup: (group: string) => void;
};

export default function ContentLayer({placements, render, onFocusGroup}: Props) {
  // First-shown order. Written during render, but only ever appended to or filtered, so repeating a render is harmless.
  const order = useRef<string[]>([]);
  const alive = new Set(placements.map(item => item.tab.id));
  const next = order.current.filter(id => alive.has(id));
  for (const item of placements) if (item.rect && !next.includes(item.tab.id)) next.push(item.tab.id);
  order.current = next;
  const byId = new Map(placements.map(item => [item.tab.id, item]));

  return (
    <div className="tab-content-layer">
      {next.map(id => {
        const {tab, group, rect} = byId.get(id)!;
        const panel = panelClass(tab.kind);
        return (
          <div
            key={id}
            id={'panel-' + id}
            role="tabpanel"
            aria-labelledby={'tab-' + id}
            className={'tab-content dock-' + panel}
            data-tab={id}
            data-kind={tab.kind}
            data-panel={panel}
            hidden={!rect}
            inert={!rect}
            style={rect ? {left: rect.x, top: rect.y, width: rect.w, height: rect.h} : undefined}
            onFocusCapture={() => onFocusGroup(group)}
            onPointerDownCapture={() => onFocusGroup(group)}
          >
            <div className="dock-pane-content">{render(tab, {visible: !!rect})}</div>
          </div>
        );
      })}
    </div>
  );
}
