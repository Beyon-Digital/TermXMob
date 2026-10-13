import {useState} from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from './ui/dialog';
afterEach(cleanup);
function ControlledDialogs({onCloseAutoFocus}:{onCloseAutoFocus?:(event:Event)=>void}){
 const [open,setOpen]=useState(false),[nested,setNested]=useState(false);
 return <><button onClick={()=>setOpen(true)}>Session options</button><button onClick={()=>setOpen(true)}>Other opener</button><button>Custom destination</button><Dialog open={open} onOpenChange={setOpen}><DialogContent onCloseAutoFocus={onCloseAutoFocus}><DialogTitle>Options</DialogTitle><DialogDescription>Choose settings.</DialogDescription><button onClick={()=>setNested(true)}>Nested options</button><Dialog open={nested} onOpenChange={setNested}><DialogContent><DialogTitle>Nested</DialogTitle><DialogDescription>Nested settings.</DialogDescription><button>Nested action</button></DialogContent></Dialog></DialogContent></Dialog></>;
}
it('returns Escape focus to the actual opener on each controlled dialog mount',async()=>{
 const user=userEvent.setup();render(<ControlledDialogs/>);
 await user.click(screen.getByRole('button',{name:'Session options'}));
 await screen.findByRole('dialog',{name:'Options'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Session options'})).toHaveFocus());
 await user.click(screen.getByRole('button',{name:'Other opener'}));
 await screen.findByRole('dialog',{name:'Options'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Other opener'})).toHaveFocus());
});
it('restores nested dialogs to their parent opener before restoring the parent',async()=>{
 const user=userEvent.setup();render(<ControlledDialogs/>);
 await user.click(screen.getByRole('button',{name:'Session options'}));
 await user.click(await screen.findByRole('button',{name:'Nested options'}));
 await screen.findByRole('dialog',{name:'Nested'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Nested options'})).toHaveFocus());
 await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Session options'})).toHaveFocus());
});
it('honors a caller that prevents default focus restoration',async()=>{
 const handler=vi.fn((event:Event)=>{event.preventDefault();screen.getByRole('button',{name:'Custom destination'}).focus()});
 const user=userEvent.setup();render(<ControlledDialogs onCloseAutoFocus={handler}/>);
 await user.click(screen.getByRole('button',{name:'Session options'}));
 await screen.findByRole('dialog',{name:'Options'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Custom destination'})).toHaveFocus());
 expect(handler).toHaveBeenCalledOnce();
});
it('runs caller close handlers before restoring focus when default is allowed',async()=>{
 const handler=vi.fn();const user=userEvent.setup();render(<ControlledDialogs onCloseAutoFocus={handler}/>);
 await user.click(screen.getByRole('button',{name:'Session options'}));
 await screen.findByRole('dialog',{name:'Options'});await user.click(screen.getByRole('button',{name:'Close'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Session options'})).toHaveFocus());expect(handler).toHaveBeenCalledOnce();
});

it('keeps the opener when a forwarded callback ref changes during an open dialog',async()=>{
 function ChangingRef(){const [open,setOpen]=useState(false),[count,setCount]=useState(0);return <><button onClick={()=>setOpen(true)}>Open changing ref</button><Dialog open={open} onOpenChange={setOpen}><DialogContent ref={()=>{}}><DialogTitle>Changing ref</DialogTitle><DialogDescription>Focus stays with the opener.</DialogDescription><button onClick={()=>setCount(count+1)}>Update {count}</button></DialogContent></Dialog></>}
 const user=userEvent.setup();render(<ChangingRef/>);
 await user.click(screen.getByRole('button',{name:'Open changing ref'}));
 await user.click(await screen.findByRole('button',{name:'Update 0'}));
 await screen.findByRole('button',{name:'Update 1'});await user.keyboard('{Escape}');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Open changing ref'})).toHaveFocus());
});
