export type SidebarPreference={hover:boolean;open:boolean};
export function readSidebarPreference(owner:string,storage:Pick<Storage,'getItem'>=localStorage):SidebarPreference|null{
 try{const value=JSON.parse(storage.getItem('termx-sidebar-v1:'+owner)||'null');return value&&typeof value.hover==='boolean'&&typeof value.open==='boolean'?{hover:value.hover,open:value.hover?false:value.open}:null}catch{return null}
}
export function saveSidebarPreference(owner:string,value:SidebarPreference,storage:Pick<Storage,'setItem'>=localStorage){
 try{storage.setItem('termx-sidebar-v1:'+owner,JSON.stringify({hover:value.hover,open:value.hover?false:value.open}))}catch{/* Navigation remains usable when storage is unavailable. */}
}
