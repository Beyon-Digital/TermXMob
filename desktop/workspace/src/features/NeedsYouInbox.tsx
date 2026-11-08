import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import type {Bot} from '../lib/bots';
import {needsYouCount,type NeedsYouItem} from '../lib/bot-schedules';
import {ActionButton,date} from './manager-ui';
import type {Mutation} from './use-manager';
import {gql} from './use-manager';

export type BotTab='overview'|'schedules'|'messages';

/**
 * What the bots are waiting on the owner for. Text in an item was written by a model or an earlier run, so it is shown as
 * plain text and never acted on. Approvals are answered in the bot's conversation, where the full request is shown.
 */
export default function NeedsYouInbox({items,bots,mutation,onOpen,showBot=true,empty}:{
  items:NeedsYouItem[];bots:Bot[];mutation:Mutation;onOpen:(botId:string,tab:BotTab)=>void;showBot?:boolean;empty?:string;
}){
  const {busy,mutate}=mutation;
  if(!items.length)return empty?<p className="manager-help">{empty}</p>:null;
  const conversation=(botId:string)=>bots.find(bot=>bot.id===botId)?.conversation_id||'';
  const dismiss=(item:NeedsYouItem)=>mutate(()=>gql('mutation($c:String!,$r:String!){acknowledge_schedule_run(conversation_id:$c,run_id:$r){id}}',{c:conversation(item.bot_id),r:item.run_id}),'Run dismissed');
  const resolve=(item:NeedsYouItem,keep:boolean)=>mutate(()=>gql('mutation($id:String!,$keep:Boolean!){resolve_memory_proposal(memory_id:$id,keep:$keep){id}}',{id:item.memory_id,keep}),keep?'Memory kept':'Memory discarded');
  const dismissMessage=(item:NeedsYouItem)=>mutate(()=>gql('mutation($message_id:String!,$status:String!){mark_message(message_id:$message_id,status:$status){id}}',{message_id:item.message_id,status:'done'}),'Message dismissed');
  return <section className="needs-you" aria-label="Needs you">
    <h3>Needs you <Badge variant="outline">{needsYouCount(items.length)}</Badge></h3>
    <ul className="needs-you-list">
      {items.map(item=><li key={item.id}>
        <div className="needs-you-text">
          <strong>{item.title}</strong>
          {item.detail&&<p>{item.detail}</p>}
          <small>{[showBot&&item.bot_name,item.scheduled&&'Scheduled run',item.created_at&&date(item.created_at)].filter(Boolean).join(' · ')}</small>
        </div>
        <div className="needs-you-actions">
          {item.kind==='approval'&&<a className="bot-link" href={'/?session='+encodeURIComponent(conversation(item.bot_id))}>Review approval</a>}
          {item.kind==='run'&&<ActionButton busy={busy} onClick={()=>void dismiss(item)}>Dismiss run</ActionButton>}
          {item.kind==='schedule'&&<Button variant="secondary" onClick={()=>onOpen(item.bot_id,'schedules')}>Review schedules</Button>}
          {item.kind==='message'&&<>
            <Button variant="secondary" onClick={()=>onOpen(item.bot_id,'messages')}>Open messages</Button>
            <ActionButton busy={busy} onClick={()=>void dismissMessage(item)}>Dismiss message</ActionButton>
          </>}
          {item.kind==='memory'&&<>
            <ActionButton busy={busy} primary onClick={()=>void resolve(item,true)}>Keep memory</ActionButton>
            <ActionButton busy={busy} onClick={()=>void resolve(item,false)}>Discard memory</ActionButton>
          </>}
        </div>
      </li>)}
    </ul>
  </section>;
}
