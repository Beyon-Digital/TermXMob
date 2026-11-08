import {useEffect,useRef,useState} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import type {Bot} from '../lib/bots';
import {needsYouCount,type NeedsYouItem} from '../lib/bot-schedules';
import {ActionButton,date} from './manager-ui';
import type {Mutation} from './use-manager';
import {gql} from './use-manager';

const PREVIEW=5;
export type BotTab='overview'|'schedules'|'messages';

/**
 * What the bots are waiting on the owner for. Text in an item was written by a model or an earlier run, so it is shown as
 * plain text and never acted on. Approvals are answered in the bot's conversation, where the full request is shown.
 */
export default function NeedsYouInbox({items,bots,mutation,onOpen,showBot=true,empty}:{
  items:NeedsYouItem[];bots:Bot[];mutation:Mutation;onOpen:(botId:string,tab:BotTab)=>void;showBot?:boolean;empty?:string;
}){
  const {busy,mutate}=mutation;
  const [showAll,setShowAll]=useState(false);
  // An answered item leaves the list, taking the focused button with it, so focus moves to the list's heading first.
  // When the last item goes the heading goes too, so a short line takes its place and receives focus instead.
  // Only answering the last item arms that; an empty list that arrives by polling never takes focus.
  const anchor=useRef<HTMLHeadingElement&HTMLParagraphElement>(null);
  const [answered,setAnswered]=useState(false),focusPending=useRef(false),none=!items.length;
  useEffect(()=>{if(none&&focusPending.current){focusPending.current=false;anchor.current?.focus()}},[none]);
  useEffect(()=>{if(!none)setAnswered(false)},[none]);
  if(none){
    const text=empty||(answered?'Nothing needs you now.':'');
    return text?<p className="manager-help" tabIndex={-1} ref={anchor}>{text}</p>:null;
  }
  const settled=<T,>(work:Promise<T|null>)=>work.then(result=>{if(result!==null){if(items.length===1){focusPending.current=true;setAnswered(true)}anchor.current?.focus()}return result});
  const conversation=(botId:string)=>bots.find(bot=>bot.id===botId)?.conversation_id||'';
  const dismiss=(item:NeedsYouItem)=>settled(mutate(()=>gql('mutation($c:String!,$r:String!){acknowledge_schedule_run(conversation_id:$c,run_id:$r){id}}',{c:conversation(item.bot_id),r:item.run_id}),'Run dismissed'));
  const resolve=(item:NeedsYouItem,keep:boolean)=>settled(mutate(()=>gql('mutation($id:String!,$keep:Boolean!){resolve_memory_proposal(memory_id:$id,keep:$keep){id}}',{id:item.memory_id,keep}),keep?'Memory kept':'Memory discarded'));
  const dismissMessage=(item:NeedsYouItem)=>settled(mutate(()=>gql('mutation($message_id:String!,$status:String!){mark_message(message_id:$message_id,status:$status){id}}',{message_id:item.message_id,status:'done'}),'Message dismissed'));
  // Paused schedules of one bot read as one line, and a long list shows its first few with the rest a click away.
  const rows:{item:NeedsYouItem;count:number}[]=[];
  for(const item of items){
    const same=item.kind==='schedule'?rows.find(row=>row.item.kind==='schedule'&&row.item.bot_id===item.bot_id):undefined;
    if(same)same.count++;else rows.push({item,count:1});
  }
  const visible=showAll?rows:rows.slice(0,PREVIEW);
  const named=(label:string,item:NeedsYouItem)=>`${label}: ${item.title}`;
  return <section className="needs-you" aria-label="Needs you">
    <h3 tabIndex={-1} ref={anchor}>Needs you <Badge variant="outline">{needsYouCount(items.length)}</Badge></h3>
    <ul className="needs-you-list">
      {visible.map(({item,count})=><li key={item.id}>
        <div className="needs-you-text">
          <strong id={'needs-'+item.id}>{count>1?`${count} schedules are paused until you re-authorize them`:item.title}</strong>
          {item.detail&&<p>{item.detail}</p>}
          <small>{[showBot&&item.bot_name,item.scheduled&&'Scheduled run',item.created_at&&date(item.created_at)].filter(Boolean).join(' · ')}</small>
        </div>
        <div className="needs-you-actions">
          {item.kind==='approval'&&<a className="bot-link" aria-describedby={'needs-'+item.id} href={'/?session='+encodeURIComponent(conversation(item.bot_id))}>Review approval</a>}
          {item.kind==='run'&&<ActionButton busy={busy} aria-describedby={'needs-'+item.id} aria-label={named('Dismiss run',item)} onClick={()=>void dismiss(item)}>Dismiss run</ActionButton>}
          {item.kind==='schedule'&&<Button variant="secondary" aria-describedby={'needs-'+item.id} onClick={()=>onOpen(item.bot_id,'schedules')}>Review schedules</Button>}
          {item.kind==='message'&&<>
            <Button variant="secondary" aria-describedby={'needs-'+item.id} aria-label={named('Open messages',item)} onClick={()=>onOpen(item.bot_id,'messages')}>Open messages</Button>
            <ActionButton busy={busy} aria-describedby={'needs-'+item.id} aria-label={named('Dismiss message',item)} onClick={()=>void dismissMessage(item)}>Dismiss message</ActionButton>
          </>}
          {item.kind==='memory'&&<>
            <ActionButton busy={busy} primary aria-describedby={'needs-'+item.id} aria-label={named('Keep memory',item)} onClick={()=>void resolve(item,true)}>Keep memory</ActionButton>
            <ActionButton busy={busy} aria-describedby={'needs-'+item.id} aria-label={named('Discard memory',item)} onClick={()=>void resolve(item,false)}>Discard memory</ActionButton>
          </>}
        </div>
      </li>)}
    </ul>
    {rows.length>PREVIEW&&<Button variant="ghost" aria-expanded={showAll} onClick={()=>setShowAll(value=>!value)}>{showAll?'Show fewer':`Show all ${rows.length}`}</Button>}
  </section>;
}
