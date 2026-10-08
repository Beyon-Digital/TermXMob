import {useState} from 'react';
import {Button} from './ui/button';
import {Input} from './ui/input';
import '../features/creation.css';
import type {NamedLayout} from '../lib/layout-presets';
export default function LayoutPresets({presets,onSave,onRestore,onRemove,onReset}:{presets:NamedLayout[];onSave:(name:string)=>void;onRestore:(id:string)=>void;onRemove:(id:string)=>void;onReset:()=>void}){
 const [name,setName]=useState(''),[query,setQuery]=useState('');
 return <section className="layout-presets" aria-label="Saved layout presets"><p>Save panel placement, visibility and sizes for this account on this device. Conversations, files, task permissions and terminal sessions stay independent.</p><form onSubmit={event=>{event.preventDefault();onSave(name)}}><label>Layout name<Input required maxLength={80} value={name} onChange={event=>setName(event.target.value)}/></label><Button type="submit" disabled={!name.trim()}>Save current layout</Button></form><label>Search saved layouts<Input type="search" value={query} onChange={event=>setQuery(event.target.value)}/></label>{presets.filter(item=>item.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())).map(item=><article key={item.id} aria-label={'Saved layout '+item.name}><strong>{item.name}</strong><small> · {item.snapshot.layout}</small><Button variant="outline" onClick={()=>onRestore(item.id)}>Restore {item.name}</Button><Button variant="ghost" onClick={()=>onRemove(item.id)}>Remove {item.name}</Button></article>)}{!presets.length&&<p>No named layouts saved yet.</p>}<Button variant="outline" onClick={onReset}>Reset current layout preset</Button></section>
}
