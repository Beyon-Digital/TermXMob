import { expect, it, vi } from 'vitest';
import { terminalKeyHandler } from './terminal-keyboard';
it('escapes backward to the toolbar without sending shell input or trapping keyboard focus',()=>{
 const leave=vi.fn(),handler=terminalKeyHandler(leave);
 const event=new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,cancelable:true});
 expect(handler(event)).toBe(false);expect(event.defaultPrevented).toBe(true);expect(leave).toHaveBeenCalledOnce();
 expect(handler(new KeyboardEvent('keyup',{key:'Tab',shiftKey:true}))).toBe(false);expect(leave).toHaveBeenCalledOnce();
});
it('retains plain shell completion, interrupt, and modified keyboard sequences',()=>{
 const leave=vi.fn(),handler=terminalKeyHandler(leave);
 for(const options of [{key:'Tab'},{key:'c',ctrlKey:true},{key:'Tab',shiftKey:true,ctrlKey:true},{key:'Tab',shiftKey:true,metaKey:true},{key:'Escape'}]) {
   const event=new KeyboardEvent('keydown',{...options,cancelable:true});expect(handler(event)).toBe(true);expect(event.defaultPrevented).toBe(false);
 }
 expect(leave).not.toHaveBeenCalled();
});
