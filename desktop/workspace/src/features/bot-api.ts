import {BOT_FIELDS,type Bot,type ProjectChoice} from '../lib/bots';
import {gql,type Mutation} from './use-manager';

/** What every tab of a bot's detail view receives. `mutation` is the section's shared write, which reloads the list. */
export interface BotTabProps { bot: Bot; bots: Bot[]; mutation: Mutation; projects: ProjectChoice[] }

export const patchBot=(id:string,input:Record<string,unknown>)=>gql<{patch_bot:Bot}>(
  `mutation($id:String!,$input:BotPatchInput!){patch_bot(bot_id:$id,input:$input){ ${BOT_FIELDS} }}`,{id,input});
