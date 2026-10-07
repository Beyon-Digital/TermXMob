import {Select} from '../components/ui/select';
export type AgentPreset={id:string;name:string;description?:string;engine:string;provider_id?:string;model?:string;workspace_revision:string};
export default function PresetPicker({presets,value,onChange,disabled=false,loading=false,label='Session agent preset'}:{presets:AgentPreset[];value?:string|null;onChange:(id:string|null)=>void;disabled?:boolean;loading?:boolean;label?:string}){
 const selected=presets.find(row=>row.id===value);
 return <label>{label}<Select aria-label={label} value={value||''} disabled={disabled||loading} onChange={event=>onChange(event.target.value||null)}><option value="">Default agent · no preset</option>{value&&!selected&&<option value={value} disabled>{loading?'Loading selected preset…':'Selected preset unavailable'}</option>}{presets.map(row=><option key={row.id} value={row.id}>{row.name}{row.engine&&row.engine!=='inherit'?' · '+row.engine:''}</option>)}</Select>{selected?.description&&<small>{selected.description}</small>}</label>
}
