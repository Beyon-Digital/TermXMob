import './AppearancePicker.css';
import {Sun,Moon,Monitor} from 'lucide-react';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
import type {ThemePreference} from '../lib/theme';
export default function AppearancePicker({value,onChange,open,onOpenChange}:{value:ThemePreference;onChange:(value:ThemePreference)=>void;open:boolean;onOpenChange:(open:boolean)=>void}){
 return <><button aria-label="Appearance" title={'Appearance: '+value} onClick={()=>onOpenChange(true)}>{value==='system'?<Monitor size={12}/>:value==='dark'?<Moon size={12}/>:<Sun size={12}/>}Appearance</button><Dialog open={open} onOpenChange={onOpenChange}><DialogContent><DialogTitle>Appearance</DialogTitle><DialogDescription>Choose your workspace theme. System follows your device appearance.</DialogDescription><div className="theme-choices" role="radiogroup" aria-label="Theme preference">{(['dark','light','system'] as const).map(preference=><label className="theme-choice" data-selected={value===preference} key={preference}><input type="radio" name="theme-preference" value={preference} checked={value===preference} onChange={()=>onChange(preference)}/><span>{preference[0].toUpperCase()+preference.slice(1)}</span>{preference==='system'?<Monitor size={18}/>:preference==='dark'?<Moon size={18}/>:<Sun size={18}/>}</label>)}</div></DialogContent></Dialog></>;
}
