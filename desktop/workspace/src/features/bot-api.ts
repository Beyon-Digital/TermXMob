import {useEffect,useState} from 'react';
import {request,type Session} from '../lib/api';
import {BOT_FIELDS,fallbackToolIds,type Bot,type ProjectChoice} from '../lib/bots';
import {gql,scoped,type Mutation} from './use-manager';

/** What every tab of a bot's detail view receives. `mutation` is the section's shared write, which reloads the list. */
export interface BotTabProps { bot: Bot; bots: Bot[]; mutation: Mutation; projects: ProjectChoice[] }

/** An installed skill as the host lists it. Every one was installed through a permission review and is enabled. */
export interface SkillExtension { id: string; version?: string; description?: string; permissions?: string[]; computer_apps?: string[] }

/** The bot's conversation (which skills it chose) and the skills the host has, for the Skills and Schedules tabs. */
export async function loadBotSkills(bot: Pick<Bot, 'conversation_id' | 'project_id'>) {
  const [session, list] = await Promise.all([
    request<Session>(`/api/workspace/sessions/${bot.conversation_id}?turns=false`),
    request<{extensions?: SkillExtension[]}>('/api/workspace/available-extensions' + scoped(bot.project_id || null))]);
  return {session, extensions: list?.extensions || []};
}

export const patchBot=(id:string,input:Record<string,unknown>)=>gql<{patch_bot:Bot}>(
  `mutation($id:String!,$input:BotPatchInput!){patch_bot(bot_id:$id,input:$input){ ${BOT_FIELDS} }}`,{id,input});

/**
 * The tool names this host has, so the ceiling editor lists what exists instead of a copy that can drift.
 * Until the host answers the list is empty (the editor then shows the bot's own tools only), so a tool this host lacks
 * cannot be ticked in the gap. If the query fails, the built-in list is used; the host rejects any name it does not know.
 */
export function useHostToolNames(){
  const [names,setNames]=useState<string[]>([]);
  useEffect(()=>{
    let live=true;
    gql<{agent_tool_names?:string[]}>('query{agent_tool_names}').then(data=>{
      const list=data?.agent_tool_names;
      if(live)setNames(Array.isArray(list)&&list.length&&list.every(item=>typeof item==='string')?list:fallbackToolIds);
    }).catch(()=>{if(live)setNames(fallbackToolIds)});
    return()=>{live=false};
  },[]);
  return names;
}
