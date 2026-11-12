/** The Advanced SQL panel's size and offset, remembered per person in this browser. */
export type PanelLayout = {width?: number; height?: number; x: number; y: number};
export const PANEL_DEFAULT: PanelLayout = {x: 0, y: 0};
const key = (ownerId?: string) => 'termx.data.advanced-layout.' + (ownerId || 'managed');
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function readPanelLayout(ownerId?: string): PanelLayout {
  try {
    const value = JSON.parse(window.localStorage.getItem(key(ownerId)) || 'null') as Partial<PanelLayout> | null;
    if (!value || typeof value !== 'object') return PANEL_DEFAULT;
    return {x: finite(value.x) ? value.x : 0, y: finite(value.y) ? value.y : 0,
      ...(finite(value.width) && value.width >= 320 ? {width: value.width} : {}), ...(finite(value.height) && value.height >= 200 ? {height: value.height} : {})};
  } catch {return PANEL_DEFAULT;}
}
export function writePanelLayout(ownerId: string | undefined, layout: PanelLayout) {
  try {
    if (layout.x === 0 && layout.y === 0 && layout.width === undefined && layout.height === undefined) window.localStorage.removeItem(key(ownerId));
    else window.localStorage.setItem(key(ownerId), JSON.stringify(layout));
  } catch {/* storage can be blocked; the panel then opens at its default size */}
}
/** Keep the panel reachable: never move it further than half the window away from where it opens. */
export const clampOffset = (value: number, limit: number) => Math.max(-limit / 2, Math.min(limit / 2, Math.round(value)));
