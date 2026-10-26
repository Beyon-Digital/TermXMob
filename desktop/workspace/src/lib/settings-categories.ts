/** One registry for legacy routes, grouped settings navigation and the mobile picker. */
export const settingsGroups=['Preferences','Workers','Host'] as const;
export const settingsCategories = [
 {id:'general',label:'General',group:'Preferences',description:'Everyday preferences for your account.'},
 {id:'models',label:'Models & engines',group:'Workers',description:'Choose engines and default models.'},
 {id:'cloud',label:'Cloud accounts',group:'Workers',description:'Connect accounts used by your agents.'},
 {id:'agents',label:'Agents',group:'Workers',description:'Manage how your agents run.'},
 {id:'extensions',label:'Plugins & skills',group:'Workers',description:'Add skills and plugins to your agents.'},
 {id:'connections',label:'Tools & connections',group:'Workers',description:'Connect the tools your agents use.'},
 {id:'memory',label:'Memory',group:'Host',description:'Control what your agents remember.'},
 {id:'hooks',label:'Hooks',group:'Host',description:'Run actions when agent events occur.'},
 {id:'automations',label:'Automations',group:'Host',description:'Schedule work on this host.'},
 {id:'runners',label:'Runners',group:'Host',description:'Choose where your agents run.'},
 {id:'access',label:'Access & sessions',group:'Host',description:'Manage access to this host.'},
 {id:'characters',label:'Characters',group:'Preferences',description:'Choose your agent’s character.'},
 {id:'appearance',label:'Appearance',group:'Preferences',description:'Choose a theme for this device.'},
 {id:'shortcuts',label:'Keyboard shortcuts',group:'Preferences',description:'Move faster with keyboard shortcuts.'},
 {id:'safety',label:'Safety & Auto Review',group:'Host',description:'Control approvals and agent permissions.'},
] as const;
const aliases:Record<string,string>={plugins:'extensions',skills:'extensions',engines:'models',schedules:'automations',goals:'automations',sessions:'access',mcp:'connections'};
export function canonicalSettingsCategory(value:string){return aliases[value]||value||'general'}
export function isSettingsCategory(value:string){return !!value&&settingsCategories.some(row=>row.id===canonicalSettingsCategory(value))}
export function settingsCategoryDescription(value:string){return (settingsCategories.find(row=>row.id===canonicalSettingsCategory(value))||settingsCategories[0]).description}
