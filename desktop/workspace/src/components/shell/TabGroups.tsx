import * as Tabs from '@radix-ui/react-tabs';
import {Check, Ellipsis, Plus, X} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from 'react';
import {
  LIMITS,
  allGroups,
  allTabs,
  editorGroups,
  findGroup,
  findSplit,
  isEditorGroup,
  type Group,
  type Layout,
  type Tab,
  type TabKind,
} from '../../lib/layout/model';
import {focusedTab, type LayoutAction} from '../../lib/layout/reduce';
import {
  bodyRect,
  dropIndex,
  dropTarget,
  layoutRects,
  phoneStack,
  placements,
  type DropTarget,
  type Handle,
  type Placement,
  type Rects,
} from '../../lib/layout/rects';
import {Button} from '../ui/button';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import ContentLayer, {type TabRender} from './ContentLayer';
import {KIND_ICON, KIND_LABEL, OPENABLE_KINDS} from './tab-meta';
import './layout.css';

/**
 * The layout tree on screen: tab groups, splitters, drag and drop, and the flat content layer.
 *
 * Geometry is computed once per render by `layoutRects` from the layout and the measured size; groups, splitters, drop
 * zones and every tab's content box are placed from those rectangles. The tab strip is a Radix tablist (roving focus,
 * arrows, Home and End, automatic activation). A tab is moved by dragging it (pointer events) or from its menu; the menu
 * route is the keyboard route.
 */

export type TabGroupsProps = {
  layout: Layout;
  dispatch: (action: LayoutAction) => void;
  titleOf: (tab: Tab) => string;
  render: TabRender;
  /** Tabs with unsaved changes get a dot. */
  dirty?: ReadonlySet<string>;
  /** Closing goes through here so a tab can ask first (Settings with unsaved edits). Defaults to closing at once. */
  onClose?: (tab: Tab) => void;
  /** "Move to new window". Return false for a tab that cannot move. Omit to hide the command. */
  onDetach?: (tab: Tab) => void;
  canDetach?: (tab: Tab) => boolean;
  /** Kinds the `+` menu offers. */
  openable?: readonly TabKind[];
  /** Force the one-stack phone projection on or off. By default it follows `(max-width: 767px)`. */
  phone?: boolean;
  /** Skip measuring (tests, or a host that already knows its size). */
  size?: {width: number; height: number};
  label?: string;
};

const PHONE_QUERY = '(max-width: 767px)';
const FALLBACK = {width: 1024, height: 640};

export function usePhone(): boolean {
  const [phone, setPhone] = useState(() => typeof matchMedia === 'function' && matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const media = matchMedia(PHONE_QUERY);
    const change = () => setPhone(media.matches);
    change();
    media.addEventListener?.('change', change);
    return () => media.removeEventListener?.('change', change);
  }, []);
  return phone;
}

function useMeasure(ref: RefObject<HTMLElement | null>, fixed?: {width: number; height: number}) {
  const [size, setSize] = useState(fixed ?? FALLBACK);
  const [strip, setStrip] = useState(36);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const read = () => {
      const box = node.getBoundingClientRect();
      const tab = parseFloat(getComputedStyle(node).getPropertyValue('--ide-tab-h'));
      if (tab > 0) setStrip(tab);
      if (fixed) return;
      // No layout to read (jsdom, or a container that is display: none): keep the last size, or the fallback.
      if (typeof ResizeObserver === 'undefined' || box.width === 0 || box.height === 0) return;
      setSize(previous =>
        previous.width === Math.round(box.width) && previous.height === Math.round(box.height)
          ? previous
          : {width: Math.round(box.width), height: Math.round(box.height)},
      );
    };
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(read);
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref, fixed?.width, fixed?.height]);
  return {size: fixed ?? size, strip};
}

export default function TabGroups(props: TabGroupsProps) {
  const {layout, dispatch, titleOf, render, dirty, onClose, onDetach, canDetach, label} = props;
  const openable = props.openable ?? OPENABLE_KINDS;
  const detectedPhone = usePhone();
  const phone = props.phone ?? detectedPhone;
  const root = useRef<HTMLDivElement>(null);
  const {size, strip} = useMeasure(root, props.size);
  const rects = layoutRects(layout, size.width, size.height);
  const close = useCallback((tab: Tab) => (onClose ? onClose(tab) : dispatch({type: 'closeTab', tab: tab.id})), [onClose, dispatch]);

  // Phone: every tab in one stack. The user's choice among them is local state, never written to the layout.
  const stack = phoneStack(layout);
  const [chosen, setChosen] = useState<string | null>(null);
  const focused = focusedTab(layout);
  useEffect(() => {
    if (focused) setChosen(focused.id);
  }, [focused?.id]);
  const projected = phoneStack(layout, chosen);

  const phoneGroup: Group = {id: 'phone', tabs: projected.tabs.map(item => item.tab), active: projected.active};
  const phoneRects: Rects = {width: size.width, height: size.height, groups: {phone: {x: 0, y: 0, w: size.width, h: size.height}}, handles: []};
  const items: Placement[] = phone
    ? projected.tabs.map(item => ({
        tab: item.tab,
        group: item.group,
        rect: item.tab.id === projected.active ? bodyRect(phoneRects.groups.phone, strip) : null,
      }))
    : placements(layout, rects, strip);

  const drag = useTabDrag({root, layout, rects, strip, dispatch, enabled: !phone, titleOf});
  const editors = editorGroups(layout.root);
  const groupLabel = (group: Group) =>
    group.id === layout.panel.group.id ? 'Panel' : 'Editor group ' + (editors.findIndex(item => item.id === group.id) + 1);

  const strips = (group: Group, rect: {x: number; y: number; w: number; h: number}, isPhone: boolean) => (
    <GroupFrame
      key={group.id}
      group={group}
      rect={rect}
      label={isPhone ? (label ?? 'Workspace') : groupLabel(group)}
      focused={isPhone || layout.focus === group.id}
      phone={isPhone}
      layout={layout}
      dispatch={dispatch}
      titleOf={titleOf}
      dirty={dirty}
      openable={openable}
      onClose={close}
      onDetach={onDetach}
      canDetach={canDetach}
      onSelect={isPhone ? setChosen : undefined}
      dragging={drag.state?.tab.id}
      onDragStart={drag.begin}
    />
  );

  return (
    <div
      ref={root}
      className="tab-workspace"
      data-phone={phone || undefined}
      data-dragging={drag.state ? '' : undefined}
      aria-label={label}
    >
      {phone
        ? strips(phoneGroup, phoneRects.groups.phone, true)
        : allGroups(layout)
            .filter(group => rects.groups[group.id])
            .map(group => strips(group, rects.groups[group.id], false))}
      {!phone && rects.handles.map(handle => <Splitter key={handle.id} handle={handle} layout={layout} rects={rects} root={root} dispatch={dispatch} />)}
      <ContentLayer
        placements={items}
        render={render}
        onFocusGroup={group => {
          if (!phone) dispatch({type: 'focusGroup', group});
        }}
      />
      {drag.state && <DropHint layout={layout} rects={rects} strip={strip} target={drag.state.target} />}
      {drag.state && (
        <div className="tab-drag-chip" style={{left: drag.state.x + 12, top: drag.state.y + 12}} aria-hidden="true">
          {titleOf(drag.state.tab)}
        </div>
      )}
    </div>
  );
}

// ---- one group -----------------------------------------------------------------------------------------------------

type FrameProps = {
  group: Group;
  rect: {x: number; y: number; w: number; h: number};
  label: string;
  focused: boolean;
  phone: boolean;
  layout: Layout;
  dispatch: (action: LayoutAction) => void;
  titleOf: (tab: Tab) => string;
  dirty?: ReadonlySet<string>;
  openable: readonly TabKind[];
  onClose: (tab: Tab) => void;
  onDetach?: (tab: Tab) => void;
  canDetach?: (tab: Tab) => boolean;
  onSelect?: (tab: string) => void;
  dragging?: string;
  onDragStart: (event: ReactPointerEvent, tab: Tab) => void;
};

function GroupFrame(props: FrameProps) {
  const {group, rect, label, focused, phone, layout, dispatch, titleOf, dirty, openable, onClose, onDetach, canDetach} = props;
  const [menu, setMenu] = useState<{tab: Tab; x: number; y: number} | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const active = group.tabs.find(tab => tab.id === group.active);
  // A tab's title can change after it is shown (the conversation's name arrives late) and change its width.
  const titles = group.tabs.map(titleOf).join('\u0000');

  // Keep the active tab in view when the strip overflows. Scroll the strip itself, not every ancestor: scrollIntoView would
  // also shift an `overflow: hidden` workspace sideways.
  useEffect(() => {
    const strip = list.current;
    const tab = group.active ? document.getElementById('tab-' + group.active) : null;
    if (!strip || !tab) return;
    const a = tab.getBoundingClientRect();
    const b = strip.getBoundingClientRect();
    if (a.left < b.left) strip.scrollLeft -= b.left - a.left;
    else if (a.right > b.right) strip.scrollLeft += a.right - b.right;
  }, [group.active, group.tabs.length, titles, rect.w]);

  function select(id: string) {
    if (props.onSelect) props.onSelect(id);
    else dispatch({type: 'setActive', group: group.id, tab: id});
  }

  function openMenu(event: ReactMouseEvent, tab: Tab) {
    event.preventDefault();
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    // A keyboard-opened context menu reports no pointer position: anchor it under the tab.
    const keyboard = event.clientX === 0 && event.clientY === 0;
    setMenu({tab, x: keyboard ? box.left : event.clientX, y: keyboard ? box.bottom : event.clientY});
  }

  return (
    <div
      role="group"
      aria-label={label}
      className="tab-group"
      data-group={group.id}
      data-focused={focused || undefined}
      data-panel-group={group.id === layout.panel.group.id || undefined}
      // Touching a group's strip makes it the focused group, even when the tab you touched was already the active one.
      onPointerDownCapture={() => !phone && dispatch({type: 'focusGroup', group: group.id})}
      onFocusCapture={() => !phone && dispatch({type: 'focusGroup', group: group.id})}
      style={{left: rect.x, top: rect.y, width: rect.w, height: rect.h}}
    >
      <div className="tab-strip">
        <Tabs.Root className="tab-strip-tabs" value={group.active ?? ''} onValueChange={select} activationMode="automatic">
          <Tabs.List ref={list} className="tab-list" aria-label={label + ' tabs'}>
            {group.tabs.map(tab => {
              const Icon = KIND_ICON[tab.kind];
              return (
                <Tabs.Trigger
                  key={tab.id}
                  id={'tab-' + tab.id}
                  value={tab.id}
                  aria-controls={'panel-' + tab.id}
                  className="tab"
                  data-tab-id={tab.id}
                  data-dragging={props.dragging === tab.id || undefined}
                  title={titleOf(tab)}
                  onPointerDown={event => {
                    if ((event.target as Element).closest('.tab-close')) return;
                    props.onDragStart(event, tab);
                  }}
                  onAuxClick={event => {
                    if (event.button === 1) {
                      event.preventDefault();
                      onClose(tab);
                    }
                  }}
                  onContextMenu={event => openMenu(event, tab)}
                  onKeyDown={event => {
                    if (event.key === 'Delete') onClose(tab);
                  }}
                >
                  <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
                  <span className="tab-title">{titleOf(tab)}</span>
                  {dirty?.has(tab.id) && (
                    <>
                      <span className="tab-dirty" aria-hidden="true" />
                      <span className="visually-hidden">, unsaved changes</span>
                    </>
                  )}
                  {/* Mouse close target. The keyboard routes are the tab menu, Delete and the Close tab command. */}
                  <span
                    className="tab-close"
                    aria-hidden="true"
                    onPointerDown={event => event.stopPropagation()}
                    onClick={event => {
                      event.stopPropagation();
                      onClose(tab);
                    }}
                  >
                    <X size={16} strokeWidth={1.5} />
                  </span>
                </Tabs.Trigger>
              );
            })}
          </Tabs.List>
        </Tabs.Root>
        <div className="tab-strip-actions">
          <AddMenu layout={layout} openable={openable} onOpen={kind => dispatch({type: 'openTab', kind, group: phone ? undefined : group.id})} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className="tab-action" aria-label="Tab actions" disabled={!active}>
                <Ellipsis size={16} strokeWidth={1.5} aria-hidden="true" />
              </button>
            </DropdownMenuTrigger>
            {active && (
              <DropdownMenuContent
                onCloseAutoFocus={event => {
                  event.preventDefault();
                  document.getElementById('tab-' + active.id)?.focus();
                }}
              >
                <TabMenuItems tab={active} group={group} layout={layout} phone={phone} dispatch={dispatch} onClose={onClose} onDetach={onDetach} canDetach={canDetach} />
              </DropdownMenuContent>
            )}
          </DropdownMenu>
        </div>
      </div>
      <div className="tab-group-body">
        {group.tabs.length === 0 && (
          <div className="tab-empty">
            <p>No tabs open in this group.</p>
            <AddMenu layout={layout} openable={openable} label="Open a tab" onOpen={kind => dispatch({type: 'openTab', kind, group: group.id})} />
          </div>
        )}
      </div>
      {menu && (
        <DropdownMenu open onOpenChange={open => !open && setMenu(null)}>
          <DropdownMenuTrigger asChild>
            <span className="tab-anchor" style={{left: menu.x, top: menu.y}} />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            onCloseAutoFocus={event => {
              event.preventDefault();
              document.getElementById('tab-' + menu.tab.id)?.focus();
            }}
          >
            <TabMenuItems tab={menu.tab} group={group} layout={layout} phone={phone} dispatch={dispatch} onClose={onClose} onDetach={onDetach} canDetach={canDetach} />
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

function AddMenu({layout, openable, onOpen, label}: {layout: Layout; openable: readonly TabKind[]; onOpen: (kind: TabKind) => void; label?: string}) {
  const open = new Set(allTabs(layout).map(tab => tab.kind));
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {label ? (
          <Button variant="outline">
            <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
            {label}
          </Button>
        ) : (
          <button type="button" className="tab-action" aria-label="New tab">
            <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {openable.map(kind => (
          <DropdownMenuCheckboxItem key={kind} checked={open.has(kind)} onSelect={() => onOpen(kind)}>
            {KIND_LABEL[kind]}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

type MenuProps = {
  tab: Tab;
  group: Group;
  layout: Layout;
  phone: boolean;
  dispatch: (action: LayoutAction) => void;
  onClose: (tab: Tab) => void;
  onDetach?: (tab: Tab) => void;
  canDetach?: (tab: Tab) => boolean;
};

/** What a tab can do. Used by the right-click menu and by the Tab actions button, which is the keyboard route. */
function TabMenuItems({tab, group, layout, phone, dispatch, onClose, onDetach, canDetach}: MenuProps) {
  const editors = editorGroups(layout.root);
  const splittable = !phone && isEditorGroup(layout, group.id) && allGroups(layout).length < LIMITS.groups;
  const others = group.tabs.filter(item => item.id !== tab.id && !item.pinned).length > 0;
  const groups = allGroups(layout).filter(item => item.id !== layout.panel.group.id || layout.panel.visible);
  const next = groups[(groups.findIndex(item => item.id === group.id) + 1) % groups.length];
  const detach = onDetach && (canDetach ? canDetach(tab) : true);
  return (
    <>
      <DropdownMenuItem onSelect={() => onClose(tab)}>Close</DropdownMenuItem>
      <DropdownMenuItem disabled={!others} onSelect={() => dispatch({type: 'closeOthers', tab: tab.id})}>
        Close others
      </DropdownMenuItem>
      {!phone && <DropdownMenuSeparator />}
      {!phone && (
        <DropdownMenuItem disabled={!splittable} onSelect={() => dispatch({type: 'splitGroup', group: group.id, dir: 'row', side: 'after', tab: tab.id})}>
          Split right
        </DropdownMenuItem>
      )}
      {!phone && (
        <DropdownMenuItem disabled={!splittable} onSelect={() => dispatch({type: 'splitGroup', group: group.id, dir: 'column', side: 'after', tab: tab.id})}>
          Split down
        </DropdownMenuItem>
      )}
      {!phone && groups.length > 1 && next && next.id !== group.id && (
        <DropdownMenuItem onSelect={() => dispatch({type: 'moveTab', tab: tab.id, group: next.id})}>Move to other group</DropdownMenuItem>
      )}
      {!phone && (editors.length > 1 || layout.panel.visible) && (
        <DropdownMenuItem onSelect={() => dispatch({type: 'toggleMaximize', group: group.id})}>
          {layout.maximized === group.id ? 'Restore group size' : 'Maximize group'}
        </DropdownMenuItem>
      )}
      {detach && (
        <>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => onDetach(tab)}>Move to new window</DropdownMenuItem>
        </>
      )}
      <DropdownMenuSeparator />
      <DropdownMenuItem
        onSelect={() => {
          dispatch({type: 'setActive', group: group.id, tab: tab.id});
          requestAnimationFrame(() => document.getElementById('panel-' + tab.id)?.querySelector<HTMLElement>('textarea,input,button,[tabindex="0"]')?.focus());
        }}
      >
        Reveal
      </DropdownMenuItem>
    </>
  );
}

// ---- splitters -------------------------------------------------------------------------------------------------------

const KEY_STEP = 16;

function Splitter({handle, layout, rects, root, dispatch}: {handle: Handle; layout: Layout; rects: Rects; root: RefObject<HTMLDivElement | null>; dispatch: (action: LayoutAction) => void}) {
  const [active, setActive] = useState(false);
  const row = handle.dir === 'row';
  const split = handle.split ? findSplit(layout.root, handle.split) : undefined;
  const panel = handle.kind === 'panel';
  const min = LIMITS.minFraction;

  // The current value, for the screen reader: the first pane's share of its split, or the panel height in pixels.
  const now = panel ? layout.panel.size : Math.round((split?.sizes[handle.index] ?? 0) * 100);
  const range = panel
    ? {min: LIMITS.panel.min, max: LIMITS.panel.max}
    : {min: Math.round(min * 100), max: Math.round(((split ? split.sizes[handle.index] + split.sizes[handle.index + 1] : 1) - min) * 100)};

  function moveTo(boundary: number) {
    // `boundary` is the pointer position along the drag axis, relative to the workspace.
    if (panel) {
      dispatch({type: 'setPanelSize', size: rects.height - boundary});
      return;
    }
    if (!split) return;
    const at = (boundary - handle.span.start) / Math.max(1, handle.span.length);
    const before = split.sizes.slice(0, handle.index).reduce((sum, size) => sum + size, 0);
    const pair = split.sizes[handle.index] + split.sizes[handle.index + 1];
    const floor = Math.min(min, pair / 2);
    const first = Math.max(floor, Math.min(pair - floor, at - before));
    const sizes = split.sizes.slice();
    sizes[handle.index] = first;
    sizes[handle.index + 1] = pair - first;
    dispatch({type: 'resize', split: split.id, sizes});
  }

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    const target = event.currentTarget;
    target.setPointerCapture?.(event.pointerId);
    setActive(true);
    const origin = root.current?.getBoundingClientRect();
    const move = (pointer: PointerEvent) => {
      if (!origin) return;
      moveTo(row ? pointer.clientX - origin.left : pointer.clientY - origin.top);
    };
    const end = () => {
      setActive(false);
      target.removeEventListener('pointermove', move);
      target.removeEventListener('pointerup', end);
      target.removeEventListener('pointercancel', end);
    };
    target.addEventListener('pointermove', move);
    target.addEventListener('pointerup', end);
    target.addEventListener('pointercancel', end);
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const forward = row ? 'ArrowRight' : 'ArrowDown';
    const back = row ? 'ArrowLeft' : 'ArrowUp';
    const pixels = event.shiftKey ? KEY_STEP * 4 : KEY_STEP;
    if (event.key !== forward && event.key !== back && event.key !== 'Home' && event.key !== 'End' && event.key !== 'Enter') return;
    event.preventDefault();
    if (panel) {
      // The panel grows upward: the boundary moves up when the panel gets taller.
      const size = layout.panel.size;
      const next = event.key === 'Home' ? LIMITS.panel.min : event.key === 'End' ? LIMITS.panel.max : event.key === 'Enter' ? LIMITS.panel.default : size + (event.key === back ? pixels : -pixels);
      dispatch({type: 'setPanelSize', size: next});
      return;
    }
    if (!split) return;
    const index = handle.index;
    const pair = split.sizes[index] + split.sizes[index + 1];
    const floor = Math.min(min, pair / 2);
    const delta = pixels / Math.max(1, handle.span.length);
    const first =
      event.key === 'Home' ? floor : event.key === 'End' ? pair - floor : event.key === 'Enter' ? pair / 2 : split.sizes[index] + (event.key === forward ? delta : -delta);
    const clamped = Math.max(floor, Math.min(pair - floor, first));
    const sizes = split.sizes.slice();
    sizes[index] = clamped;
    sizes[index + 1] = pair - clamped;
    dispatch({type: 'resize', split: split.id, sizes});
  }

  return (
    <div
      role="separator"
      tabIndex={0}
      aria-orientation={row ? 'vertical' : 'horizontal'}
      aria-label={panel ? 'Resize panel' : 'Resize groups'}
      aria-valuenow={now}
      aria-valuemin={range.min}
      aria-valuemax={range.max}
      aria-valuetext={panel ? now + ' pixels' : now + ' percent'}
      className="tab-splitter"
      data-dir={handle.dir}
      data-active={active || undefined}
      style={{left: handle.rect.x, top: handle.rect.y, width: handle.rect.w, height: handle.rect.h}}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}

// ---- drag and drop ---------------------------------------------------------------------------------------------------

type DragState = {tab: Tab; x: number; y: number; target: DropTarget | null};

function useTabDrag(args: {
  root: RefObject<HTMLDivElement | null>;
  layout: Layout;
  rects: Rects;
  strip: number;
  dispatch: (action: LayoutAction) => void;
  enabled: boolean;
  titleOf: (tab: Tab) => string;
}) {
  const [state, setState] = useState<DragState | null>(null);
  const latest = useRef(args);
  useLayoutEffect(() => {
    latest.current = args;
  });

  const begin = useCallback((event: ReactPointerEvent, tab: Tab) => {
    if (!latest.current.enabled || event.button !== 0 || event.pointerType === 'touch') return;
    const startX = event.clientX;
    const startY = event.clientY;
    let dragging = false;

    const locate = (x: number, y: number) => {
      const current = latest.current;
      const box = current.root.current?.getBoundingClientRect();
      return box ? dropTarget(current.layout, current.rects, x - box.left, y - box.top, current.strip) : null;
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      window.removeEventListener('keydown', key, true);
    };
    function move(pointer: PointerEvent) {
      if (!dragging && Math.hypot(pointer.clientX - startX, pointer.clientY - startY) < 6) return;
      dragging = true;
      setState({tab, x: pointer.clientX, y: pointer.clientY, target: locate(pointer.clientX, pointer.clientY)});
    }
    function up(pointer: PointerEvent) {
      stop();
      if (!dragging) return;
      setState(null);
      const target = locate(pointer.clientX, pointer.clientY);
      const {dispatch, root} = latest.current;
      if (!target) return;
      if (target.zone === 'center') {
        let index: number | undefined;
        if (target.strip) {
          const tabs = Array.from(root.current?.querySelectorAll<HTMLElement>(`[data-group="${target.group}"] [role="tab"]`) ?? []);
          index = dropIndex(tabs.map(node => ({left: node.getBoundingClientRect().left, width: node.getBoundingClientRect().width})), pointer.clientX);
        }
        dispatch({type: 'moveTab', tab: tab.id, group: target.group, index});
        return;
      }
      dispatch({
        type: 'splitGroup',
        group: target.group,
        dir: target.zone === 'left' || target.zone === 'right' ? 'row' : 'column',
        side: target.zone === 'left' || target.zone === 'top' ? 'before' : 'after',
        tab: tab.id,
      });
    }
    function cancel() {
      stop();
      setState(null);
    }
    function key(keyboard: KeyboardEvent) {
      if (keyboard.key === 'Escape') {
        keyboard.stopPropagation();
        cancel();
      }
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('keydown', key, true);
  }, []);

  return {state, begin};
}

function DropHint({layout, rects, strip, target}: {layout: Layout; rects: Rects; strip: number; target: DropTarget | null}): ReactNode {
  if (!target) return null;
  const frame = rects.groups[target.group];
  if (!frame) return null;
  const body = bodyRect(frame, strip);
  const half = {
    center: target.strip ? {x: frame.x, y: frame.y, w: frame.w, h: strip} : body,
    left: {...body, w: body.w / 2},
    right: {...body, x: body.x + body.w / 2, w: body.w / 2},
    top: {...body, h: body.h / 2},
    bottom: {...body, y: body.y + body.h / 2, h: body.h / 2},
  }[target.zone];
  return <div className="tab-drop" data-zone={target.zone} data-group={target.group} style={{left: half.x, top: half.y, width: half.w, height: half.h}} />;
}
