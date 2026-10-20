export const panels=['editor','conversation','review','browser','terminal','computer','artifacts'] as const;
export type DockPanel=typeof panels[number];
export type DockRegion='left'|'main'|'split'|'right'|'bottom'|'hidden';
export type DockGeometry={left:number;split:number;bottom:number};
export const defaultGeometry:DockGeometry={left:220,split:420,bottom:260};
export type DockMap=Record<DockPanel,DockRegion>;
export const regions:DockRegion[]=['left','main','split','right','bottom','hidden'];
const hidden:DockMap={editor:'hidden',conversation:'hidden',review:'hidden',browser:'hidden',terminal:'hidden',computer:'hidden',artifacts:'hidden'};
export const layouts:Record<string,DockMap>={chat:{...hidden,conversation:'main'},workbench:{...hidden,editor:'main',conversation:'right'},browser:{...hidden,browser:'main',conversation:'right'},computer:{...hidden,computer:'main',conversation:'right'},artifacts:{...hidden,artifacts:'main',conversation:'right'},review:{...hidden,review:'main',conversation:'right'},terminal:{...hidden,terminal:'main',conversation:'right'},focus:{...hidden,editor:'main'}};
export function validateDockMap(value:unknown,layout?:string):DockMap|null{
 if(!value||typeof value!=='object')return null;
 const map=value as Partial<DockMap>;
 if(!(['editor','conversation','browser'] as const).every(panel=>regions.includes(map[panel]!)))return null;
 for(const panel of ['computer','artifacts','review','terminal'] as const)if(map[panel]!==undefined&&!regions.includes(map[panel]!))return null;
 const restored=Object.fromEntries(panels.map(panel=>[panel,map[panel]||'hidden'])) as DockMap;
 if(map.computer===undefined&&layout==='computer'){restored.computer=restored.browser;restored.browser='hidden'}
 // Review used to open an editor/browser arrangement without a diff surface.
 if(map.review===undefined&&layout==='review'){restored.review='main';restored.editor='hidden';restored.browser='hidden'}
 return restored;
}
export function focusDockMap(panel:DockPanel):DockMap{return {...Object.fromEntries(panels.map(item=>[item,'hidden'])) as DockMap,[panel]:'main'}}
export function panelSurface(panel:DockPanel):'chat'|'workbench'|'review'|'browser'|'terminal'|'computer'|'artifacts'{return panel==='editor'?'workbench':panel==='conversation'?'chat':panel}
export function workspaceSurface(layout:string,dock:DockMap):ReturnType<typeof panelSurface>{return layout==='focus'?panelSurface(panels.find(panel=>dock[panel]==='main')||'editor'):layout==='review'?'review':layout==='terminal'?'terminal':layout==='artifacts'?'artifacts':layout==='computer'?'computer':layout==='browser'?'browser':layout==='chat'?'chat':'workbench'}
export function restoreWindowBounds(bounds:{x:number;y:number;width:number;height:number},screen:{width:number;height:number}){const width=Math.max(480,Math.min(bounds.width,screen.width)),height=Math.max(360,Math.min(bounds.height,screen.height));return {x:Math.max(0,Math.min(bounds.x,screen.width-width)),y:Math.max(0,Math.min(bounds.y,screen.height-height)),width,height};}
