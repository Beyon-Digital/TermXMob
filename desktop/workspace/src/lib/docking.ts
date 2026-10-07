export const panels=['editor','conversation','browser','computer','artifacts'] as const;
export type DockPanel=typeof panels[number];
export type DockRegion='left'|'main'|'split'|'right'|'bottom'|'hidden';
export type DockGeometry={left:number;split:number;bottom:number};
export const defaultGeometry:DockGeometry={left:220,split:420,bottom:260};
export type DockMap=Record<DockPanel,DockRegion>;
export const regions:DockRegion[]=['left','main','split','right','bottom','hidden'];
export const layouts:Record<string,DockMap>={chat:{editor:'hidden',conversation:'main',browser:'hidden',computer:'hidden',artifacts:'hidden'},workbench:{editor:'main',conversation:'right',browser:'hidden',computer:'hidden',artifacts:'hidden'},browser:{editor:'hidden',conversation:'right',browser:'main',computer:'hidden',artifacts:'hidden'},computer:{editor:'hidden',conversation:'right',browser:'hidden',computer:'main',artifacts:'hidden'},artifacts:{editor:'hidden',conversation:'right',browser:'hidden',computer:'hidden',artifacts:'main'},review:{editor:'main',conversation:'right',browser:'split',computer:'hidden',artifacts:'hidden'},focus:{editor:'main',conversation:'hidden',browser:'hidden',computer:'hidden',artifacts:'hidden'}};
export function validateDockMap(value:unknown,layout?:string):DockMap|null{
 if(!value||typeof value!=='object')return null;
 const map=value as Partial<DockMap>;
 if(!panels.slice(0,3).every(panel=>regions.includes(map[panel]!)))return null;
 for(const panel of ['computer','artifacts'] as const)if(map[panel]!==undefined&&!regions.includes(map[panel]!))return null;
 const restored=Object.fromEntries(panels.map(panel=>[panel,map[panel]||'hidden'])) as DockMap;
 // Legacy Computer occupied the Browser slot. Transfer only that saved
 // surface's placement; do not overwrite any unrelated dock or geometry.
 if(map.computer===undefined&&layout==='computer'){restored.computer=restored.browser;restored.browser='hidden'}
 return restored;
}
export function focusDockMap(panel:DockPanel):DockMap{return {...Object.fromEntries(panels.map(item=>[item,'hidden'])) as DockMap,[panel]:'main'}}
export function panelSurface(panel:DockPanel):'chat'|'workbench'|'browser'|'computer'|'artifacts'{return panel==='editor'?'workbench':panel==='conversation'?'chat':panel}
export function workspaceSurface(layout:string,dock:DockMap):'chat'|'workbench'|'browser'|'computer'|'artifacts'{return layout==='focus'?panelSurface(panels.find(panel=>dock[panel]==='main')||'editor'):layout==='review'?'workbench':layout==='artifacts'?'artifacts':layout==='computer'?'computer':layout==='browser'?'browser':layout==='chat'?'chat':'workbench'}
export function restoreWindowBounds(bounds:{x:number;y:number;width:number;height:number},screen:{width:number;height:number}){const width=Math.max(480,Math.min(bounds.width,screen.width)),height=Math.max(360,Math.min(bounds.height,screen.height));return {x:Math.max(0,Math.min(bounds.x,screen.width-width)),y:Math.max(0,Math.min(bounds.y,screen.height-height)),width,height};}
