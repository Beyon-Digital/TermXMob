/** Exit animations retain DOM nodes. Closed controls must stop accepting
 * interaction immediately rather than waiting for visual removal. */
export function observeClosedState(content: HTMLElement, onClose?: () => void) {
 const update=()=>{
  if(content.dataset.state==='closed'){
   if(content.contains(document.activeElement))onClose?.();
   content.setAttribute('inert','');
  }else content.removeAttribute('inert');
 };
 update();
 const observer=new MutationObserver(update);
 observer.observe(content,{attributes:true,attributeFilter:['data-state']});
 return observer;
}
