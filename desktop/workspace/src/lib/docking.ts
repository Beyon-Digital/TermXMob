export type DockPanel='editor'|'conversation'|'browser';
export type DockRegion='left'|'main'|'split'|'right'|'bottom'|'hidden';
export type DockGeometry={left:number;split:number;bottom:number};
export const defaultGeometry:DockGeometry={left:220,split:420,bottom:260};
export type DockMap=Record<DockPanel,DockRegion>;
export const regions:DockRegion[]=['left','main','split','right','bottom','hidden'];
export const layouts:Record<string,DockMap>={chat:{editor:'hidden',conversation:'main',browser:'hidden'},workbench:{editor:'main',conversation:'right',browser:'hidden'},browser:{editor:'hidden',conversation:'right',browser:'main'},computer:{editor:'hidden',conversation:'right',browser:'main'},review:{editor:'main',conversation:'right',browser:'split'},focus:{editor:'main',conversation:'hidden',browser:'hidden'}};
export function validateDockMap(value:unknown):DockMap|null{if(!value||typeof value!=='object')return null;const map=value as DockMap;return ['editor','conversation','browser'].every(panel=>regions.includes(map[panel as DockPanel]))?map:null;}
export function restoreWindowBounds(bounds:{x:number;y:number;width:number;height:number},screen:{width:number;height:number}){const width=Math.max(480,Math.min(bounds.width,screen.width)),height=Math.max(360,Math.min(bounds.height,screen.height));return {x:Math.max(0,Math.min(bounds.x,screen.width-width)),y:Math.max(0,Math.min(bounds.y,screen.height-height)),width,height};}
