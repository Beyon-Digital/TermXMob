import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Alert} from '../components/ui/alert';
import {gql} from './use-manager';

interface Attempt{id:string;url:string;browserOpened:boolean;accountId:string|null}
interface StartReply{start_chatgpt_sign_in?:{attempt_id:string;authorization_url:string;browser_opened?:boolean}}
interface StatusReply{chatgpt_sign_in_status?:{status:string;message?:string}}
const POLL_MS=2000;
/** Only a real https address is offered as a link; the host builds it, but a link is never rendered on trust alone. */
const safeUrl=(value:string)=>{try{return new URL(value).protocol==='https:'?value:null}catch{return null}};

/**
 * Sign in to a ChatGPT plan from Settings. The host opens the sign-in page in its own browser and finishes the
 * sign-in when ChatGPT redirects back to it, so this only starts the attempt, shows its state and lets it be cancelled.
 * `signIn(accountId)` is exposed to the account rows through the render prop so "Sign in again" uses the same flow.
 */
export default function ChatGPTSignIn({busy,onStart,onFinished,children}:{busy:boolean;onStart:(run:()=>Promise<unknown>)=>Promise<unknown>;onFinished:(outcome:{ok:boolean;message:string})=>void;children:(signIn:(accountId:string|null)=>void,pending:boolean)=>React.ReactNode}){
  const [attempt,setAttempt]=useState<Attempt|null>(null);
  const finished=useRef(onFinished);finished.current=onFinished;
  useEffect(()=>{
    if(!attempt)return;
    let stopped=false;
    const timer=setInterval(async()=>{
      try{
        const reply=await gql<StatusReply>('query($id:String!){chatgpt_sign_in_status(attempt_id:$id)}',{id:attempt.id});
        const state=reply.chatgpt_sign_in_status;
        if(stopped||!state||state.status==='pending')return;
        stopped=true;setAttempt(null);
        finished.current({ok:state.status==='completed',message:state.message||(state.status==='completed'?'Signed in to ChatGPT.':'ChatGPT sign-in did not finish.')});
      }catch(reason){
        // The host forgets an attempt after a restart; stop waiting instead of polling a sign-in that no longer exists.
        if(stopped)return;
        stopped=true;setAttempt(null);
        finished.current({ok:false,message:reason instanceof Error?reason.message:'ChatGPT sign-in status could not be read.'});
      }
    },POLL_MS);
    return()=>{stopped=true;clearInterval(timer)};
  },[attempt]);
  const signIn=(accountId:string|null)=>{
    void onStart(async()=>{
      const reply=await gql<StartReply>('mutation($id:String){start_chatgpt_sign_in(account_id:$id)}',{id:accountId});
      const started=reply.start_chatgpt_sign_in;
      if(!started)throw new Error('The host did not start ChatGPT sign-in.');
      setAttempt({id:started.attempt_id,url:started.authorization_url,browserOpened:started.browser_opened===true,accountId});
      return reply;
    });
  };
  const cancel=()=>{
    const current=attempt;if(!current)return;
    setAttempt(null);
    void gql('mutation($id:String!){cancel_chatgpt_sign_in(attempt_id:$id)}',{id:current.id}).catch(()=>{});
  };
  const link=attempt?safeUrl(attempt.url):null;
  return <>
    {children(signIn,!!attempt)}
    {attempt&&<Alert role="status" className="chatgpt-sign-in">
      <p><strong>Waiting for ChatGPT sign-in.</strong> {attempt.browserOpened?'A sign-in page opened in the browser on the host machine.':'Open the sign-in page in a browser on the host machine.'} This finishes on its own once you approve access there, and expires after 10 minutes.</p>
      <div className="manager-item-actions">{link&&<Button variant="secondary" asChild><a href={link} target="_blank" rel="noopener noreferrer">Open sign-in page</a></Button>}<Button type="button" variant="secondary" disabled={busy} onClick={cancel}>Cancel sign-in</Button></div>
    </Alert>}
  </>;
}
