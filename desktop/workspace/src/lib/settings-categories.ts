/** One registry for legacy manager routes, the settings rail and compact tabs. */
export const settingsCategories = [
 {id:'general',label:'General'}, {id:'models',label:'Models & engines'},
 {id:'cloud',label:'Cloud accounts'}, {id:'agents',label:'Agents'},
 {id:'extensions',label:'Plugins & skills'}, {id:'connections',label:'Tools & connections'},
 {id:'memory',label:'Memory'}, {id:'hooks',label:'Hooks'},
 {id:'automations',label:'Automations'}, {id:'runners',label:'Runners'},
 {id:'access',label:'Access & sessions'}, {id:'appearance',label:'Appearance'},
 {id:'shortcuts',label:'Keyboard shortcuts'}, {id:'safety',label:'Safety & Auto Review'},
] as const;
const aliases:Record<string,string>={plugins:'extensions',skills:'extensions',engines:'models',schedules:'automations',goals:'automations',sessions:'access',mcp:'connections'};
export function canonicalSettingsCategory(value:string){return aliases[value]||value||'general'}
export function isSettingsCategory(value:string){return !!value&&settingsCategories.some(row=>row.id===canonicalSettingsCategory(value))}
