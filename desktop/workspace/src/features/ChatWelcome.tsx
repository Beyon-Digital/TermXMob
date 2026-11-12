import {ChevronRight} from 'lucide-react';
import {Button} from '../components/ui/button';
import type {Engine,Provider,Session} from '../lib/api';
import './chat-welcome.css';

const starters=[
 {title:'Make a plan',description:'Turn a goal into clear next steps',prompt:'Help me turn my goal into a practical plan. Ask what you need to know first.'},
 {title:'Research something',description:'Explore a question and compare options',prompt:'Help me research a question and compare the options. Ask me what I want to explore.'},
 {title:'Work on a task',description:'Explain the outcome you want',prompt:'I have a task for you. Help me define the outcome, then work through it with me.'},
];

export default function ChatWelcome({session,engines,providers,onPrompt,onConfigureAgent,hideConnectionHelp=false}:{hideConnectionHelp?:boolean;session:Session|null;engines:Engine[];providers:Provider[];onPrompt:(text:string)=>void;onConfigureAgent?:()=>void}){
 const selectedProvider=providers.find(provider=>provider.id===session?.provider_id);
 const selectedEngine=engines.find(engine=>engine.id===session?.engine);
 const needsConnection=!hideConnectionHelp&&!!session&&(session.engine==='internal'?(!session.runner_id&&!selectedProvider?.secret_configured):(!selectedEngine||selectedEngine.available===false));
 return <div className="chat-empty chat-welcome">
  <div className="chat-welcome-content">
   <h2>{session?'What can we work on?':'Start with a conversation'}</h2>
   {session&&<details className="chat-welcome-ideas"><summary><ChevronRight size={16} strokeWidth={1.5} aria-hidden="true"/>Need a starting point?</summary><div className="chat-starters" aria-label="Conversation ideas">{starters.map(starter=><Button key={starter.title} variant="ghost" onClick={()=>onPrompt(starter.prompt)}><span><strong>{starter.title}</strong><small>{starter.description}</small></span></Button>)}</div></details>}
   {needsConnection&&<div className="chat-connection-help"><span><strong>Connect your agent</strong><small>{session?.engine==='internal'?'Choose a model connection before your first message. Your draft stays here.':'This engine is unavailable. Check its connection before starting a task.'}</small></span>{onConfigureAgent&&<Button size="sm" variant="outline" onClick={onConfigureAgent}>Set up connection</Button>}</div>}
  </div>
 </div>;
}
