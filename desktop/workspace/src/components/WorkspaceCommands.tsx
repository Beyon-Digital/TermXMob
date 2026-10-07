import {useRef,useState} from 'react';
import {Input} from './ui/input';
import './WorkspaceCommands.css';
export type WorkspaceCommand={id:string;label:string;keywords?:string;shortcut?:string;run:()=>void};
export default function WorkspaceCommands({commands}:{commands:WorkspaceCommand[]}){
 const [query,setQuery]=useState('');const search=useRef<HTMLInputElement>(null),results=useRef<HTMLDivElement>(null);
 const terms=query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
 const matches=commands.filter(command=>terms.every(term=>(command.label+' '+(command.keywords||'')).toLocaleLowerCase().includes(term)));
 const buttons=()=>Array.from(results.current?.querySelectorAll<HTMLButtonElement>('button')||[]);
 return <section className="workspace-commands" aria-label="Search workspace commands"><Input ref={search} autoFocus type="search" aria-label="Search commands" placeholder="Search commands, views and settings" value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==='ArrowDown'&&matches.length){event.preventDefault();buttons()[0]?.focus()}else if(event.key==='Enter'&&matches.length){event.preventDefault();matches[0].run()}}}/><p className="command-count" role="status">{matches.length?matches.length+(matches.length===1?' command':' commands'):'No matching commands'}</p><div ref={results} className="command-results" onKeyDown={event=>{if(event.key!=='ArrowDown'&&event.key!=='ArrowUp')return;const rows=buttons(),index=rows.indexOf(event.target as HTMLButtonElement);if(index<0)return;event.preventDefault();const next=index+(event.key==='ArrowDown'?1:-1);if(next<0)search.current?.focus();else rows[Math.min(next,rows.length-1)]?.focus()}}>{matches.map(command=><button type="button" className="command-row" key={command.id} onClick={command.run}><span>{command.label}</span>{command.shortcut&&<kbd>{command.shortcut}</kbd>}</button>)}</div></section>;
}
