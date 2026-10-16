import {useRef,useState,type MouseEvent, type ReactNode} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
/** Shared safety/validation shell for every settings category, including the
 * older div-based forms. It reviews before dispatching the existing handler. */
export default function SettingsActions({children}:{children:ReactNode}){
 const [review,setReview]=useState<{button:HTMLButtonElement;label:string;target:string}|null>(null),[error,setError]=useState('');
 const reviewed=useRef<HTMLElement|null>(null);
 function capture(event:MouseEvent<HTMLDivElement>){
  const button=(event.target as HTMLElement).closest<HTMLButtonElement>('button');if(!button||button.disabled)return;
  if(reviewed.current===button){reviewed.current=null;return}
  const label=button.textContent?.trim()||button.getAttribute('aria-label')||'';
  if(/^(save|create|add |verify and save|authorize and create|apply|enroll|provision)/i.test(label)){
   const form=button.closest('form,.manager-form,.creation-form,.safety-config');
   const invalid=form&&Array.from(form.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('input,select,textarea')).find(field=>!field.disabled&&!field.checkValidity());
   if(invalid){event.preventDefault();event.stopPropagation();setError(invalid.validationMessage||'Review the highlighted field before saving.');invalid.reportValidity();invalid.focus();return}
  }
  setError('');
  if(/^(cancel|refresh|use current|edit |update authentication|new scheduled task|enroll)/i.test(label)&&!window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true}))){event.preventDefault();event.stopPropagation();return}
  if(/^(delete|remove|revoke|tear down|stop runner|end legacy|disable.*revoke|disable model review|prune with)/i.test(label)&&!button.closest('[data-reviewed-action]')){
   event.preventDefault();event.stopPropagation();
   const row=button.closest('article,tr,.manager-preview,.safety-rule');
   const target=row?.querySelector('h3,h2,strong,td,p')?.textContent?.trim()||'';
   setReview({button,label,target});
  }
 }
 return <div className="settings-actions" onClickCapture={capture}>{error&&<p role="alert" className="manager-error">{error}</p>}{children}<Dialog open={!!review} onOpenChange={open=>{if(!open)setReview(null)}}><DialogContent data-reviewed-action><DialogTitle>{review?.label}?</DialogTitle><DialogDescription>Review this change before it is sent to the host. Deletion is permanent; revoking access disconnects affected devices and running work.</DialogDescription>{review?.target&&<p>{review.target}</p>}<div className="manager-item-actions"><Button variant="secondary" onClick={()=>setReview(null)}>Cancel</Button><Button onClick={()=>{if(!review)return;const button=review.button;setReview(null);if(button.isConnected&&!button.disabled){reviewed.current=button;button.click()}}}>Confirm {review?.label.toLowerCase()}</Button></div></DialogContent></Dialog></div>
}
