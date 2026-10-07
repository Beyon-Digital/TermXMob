import {ChevronDown,Folder,Pin} from 'lucide-react';
export default function ProjectGroup({id,name,collapsed,pinned,busy,onSelect,onPin}:{id:string;name:string;collapsed:boolean;pinned:boolean;busy:boolean;onSelect:()=>void;onPin:()=>void}){
 return <div className="project-group-row"><button className="project-group" aria-expanded={!collapsed} onClick={onSelect}><Folder size={14}/><span>{name}</span><ChevronDown size={12} style={{transform:collapsed?'rotate(-90deg)':undefined}}/></button>{id&&<button className="project-pin" aria-label={(pinned?'Unpin project ':'Pin project ')+name} aria-pressed={pinned} disabled={busy} onClick={onPin}><Pin size={12}/></button>}</div>
}
