import {defaultGeometry,layouts,validateDockMap,type DockMap,type DockGeometry} from './docking';
export const persistentLayouts=['chat','workbench','browser','computer','artifacts','review'] as const;
export type PersistentLayout=typeof persistentLayouts[number];
export type LayoutSnapshot={layout:PersistentLayout|'focus';dock:DockMap;left:boolean;right:boolean;bottom:boolean;dockGeometry:DockGeometry;sidebarWidth:number;rightWidth:number};
export type NamedLayout={id:string;name:string;snapshot:LayoutSnapshot;updated:number};
type Book={version:1;owner:string;device:string;last:PersistentLayout|null;arrangements:Partial<Record<PersistentLayout,LayoutSnapshot>>;named:NamedLayout[]};
const dimension=(value:unknown,fallback:number,min:number,max:number)=>typeof value==='number'&&Number.isFinite(value)?Math.max(min,Math.min(max,value)):fallback;
export function layoutSnapshot(value:unknown):LayoutSnapshot|null{
 if(!value||typeof value!=='object')return null;const row=value as LayoutSnapshot;
 if(!persistentLayouts.includes(row.layout as PersistentLayout))return null;
 const dock=validateDockMap(row.dock,row.layout);if(!dock)return null;
 return {layout:row.layout,dock,left:row.left!==false,right:row.right!==false,bottom:row.bottom!==false,sidebarWidth:dimension(row.sidebarWidth,244,200,420),rightWidth:dimension(row.rightWidth,380,240,800),dockGeometry:{left:dimension(row.dockGeometry?.left,220,180,800),split:dimension(row.dockGeometry?.split,420,180,800),bottom:dimension(row.dockGeometry?.bottom,260,120,700)}};
}
export function defaultLayout(layout:PersistentLayout):LayoutSnapshot{return {layout,dock:{...layouts[layout]},left:true,right:true,bottom:true,dockGeometry:{...defaultGeometry},sidebarWidth:244,rightWidth:380}}
export function layoutDevice(storage:Storage=localStorage){const key='termx-layout-device-v1';let value=storage.getItem(key);if(!value||!/^[a-zA-Z0-9-]{8,128}$/.test(value)){value=crypto.randomUUID();storage.setItem(key,value)}return value}
export class LayoutPresetStore{
 readonly key:string;
 constructor(readonly owner:string,readonly storage:Storage=localStorage,readonly device=layoutDevice(storage)){this.key='termx-layout-presets-v1:'+JSON.stringify([owner,device])}
 read():Book{
  const empty:Book={version:1,owner:this.owner,device:this.device,last:null,arrangements:{},named:[]};
  try{const raw=this.storage.getItem(this.key);if(!raw||raw.length>256000)return empty;const row=JSON.parse(raw);if(row.version!==1||row.owner!==this.owner||row.device!==this.device)return empty;
   for(const layout of persistentLayouts){const snapshot=layoutSnapshot(row.arrangements?.[layout]);if(snapshot?.layout===layout)empty.arrangements[layout]=snapshot}
   if(persistentLayouts.includes(row.last))empty.last=row.last;
   for(const item of (Array.isArray(row.named)?row.named:[]).slice(0,30)){const snapshot=layoutSnapshot(item.snapshot);if(snapshot&&typeof item.id==='string'&&item.id.length<=128&&typeof item.name==='string'&&item.name.trim().length>0&&item.name.length<=80&&Number.isFinite(item.updated)&&!empty.named.some(saved=>saved.id===item.id))empty.named.push({id:item.id,name:item.name,snapshot,updated:item.updated})}
  }catch{}return empty;
 }
 private write(book:Book){this.storage.setItem(this.key,JSON.stringify(book))}
 remember(value:LayoutSnapshot){const snapshot=layoutSnapshot(value);if(!snapshot)return;const book=this.read();book.arrangements[snapshot.layout as PersistentLayout]=snapshot;book.last=snapshot.layout as PersistentLayout;this.write(book)}
 arrangement(layout:PersistentLayout){return this.read().arrangements[layout]||defaultLayout(layout)}
 save(name:string,value:LayoutSnapshot){const snapshot=layoutSnapshot(value);name=name.trim();if(!snapshot||!name||name.length>80||/[\x00-\x1f\x7f]/.test(name))throw Error('Name the current layout with 1–80 characters outside temporary Focus mode.');const book=this.read(),old=book.named.find(item=>item.name===name);if(!old&&book.named.length>=30)throw Error('Remove a saved layout before adding another; maximum 30.');const item={id:old?.id||crypto.randomUUID(),name,snapshot,updated:Date.now()};book.named=[...book.named.filter(row=>row.id!==item.id),item];this.write(book);return item}
 remove(id:string){const book=this.read();book.named=book.named.filter(row=>row.id!==id);this.write(book)}
 reset(layout:PersistentLayout){const book=this.read();book.arrangements[layout]=defaultLayout(layout);this.write(book);return book.arrangements[layout]!}
}
