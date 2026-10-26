import type {Task,Turn} from './api';
export type ConversationMessageRow={id:string;role:'user'|'assistant';text:string;task:Task|undefined};
/** Report turns carry an explicit backend notification marker. They retain the
 * canonical answer id without inventing a user instruction or inference turn. */
export function conversationMessages(turns:Turn[]):ConversationMessageRow[]{
 return turns.flatMap<ConversationMessageRow>(turn=>[
  ...(turn.task?.coordination_report?[]:[{id:turn.id,role:'user' as const,text:turn.prompt,task:undefined}]),
  ...(turn.task?[{id:turn.id+':answer',role:'assistant' as const,text:turn.task.result||'',task:turn.task}]:[]),
 ]);
}

/** Source reports never replace the state or recovery actions of a real run. */
export function latestExecutionTask(turns:Turn[]):Task|undefined{return [...turns].reverse().find(turn=>turn.task&&turn.task.engine!=='coordination'&&!turn.task.coordination_report)?.task}
