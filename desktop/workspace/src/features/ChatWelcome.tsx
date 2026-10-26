import {ArrowUpRight, Compass, ListChecks, Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import type {Engine,Provider,Session} from '../lib/api';
import './chat-welcome.css';

const starters=[
 {title:'Make a plan',description:'Turn a goal into clear next steps',prompt:'Help me turn my goal into a practical plan. Ask what you need to know first.',icon:ListChecks},
 {title:'Research something',description:'Explore a question and compare options',prompt:'Help me research a question and compare the options. Ask me what I want to explore.',icon:Compass},
 {title:'Work on a task',description:'Explain the outcome you want',prompt:'I have a task for you. Help me define the outcome, then work through it with me.',icon:Workflow},
];

export default function ChatWelcome({session,engines,providers,onPrompt,onConfigureAgent}:{session:Session|null;engines:Engine[];providers:Provider[];onPrompt:(text:string)=>void;onConfigureAgent?:()=>void}){
 const selectedProvider=providers.find(provider=>provider.id===session?.provider_id);
 const selectedEngine=engines.find(engine=>engine.id===session?.engine);
 const needsConnection=!!session&&(session.engine==='internal'&&!session.runner_id?(!selectedProvider?.secret_configured):selectedEngine?.available===false);
 return <div className="chat-empty chat-welcome">
  <div className="chat-welcome-content">
   <h2>{session?'What can we work on?':'Start with a conversation'}</h2>
   <p>{session?'Bring a question, a goal, or a task. Your agent keeps the conversation in one place; files and tools are here when you need them.':'Your conversations and agent activity will appear here. Open a chat to continue.'}</p>
   {session&&<div className="chat-starters" aria-label="Conversation ideas">{starters.map(starter=>{const Icon=starter.icon;return <button key={starter.title} type="button" onClick={()=>onPrompt(starter.prompt)}><Icon size={18} aria-hidden="true"/><span><strong>{starter.title}</strong><small>{starter.description}</small></span><ArrowUpRight size={14} aria-hidden="true"/></button>})}</div>}
   {needsConnection&&<div className="chat-connection-help"><span><strong>Connect your agent</strong><small>{session?.engine==='internal'?'Choose a model connection before your first message. Your draft stays here.':'This engine is unavailable. Check its connection before starting a task.'}</small></span>{onConfigureAgent&&<Button size="sm" variant="outline" onClick={onConfigureAgent}>Set up connection</Button>}</div>}
  </div>
 </div>;
}
