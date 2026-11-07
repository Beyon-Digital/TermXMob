import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import SessionModePicker from './SessionModePicker';
import {type Session} from '../lib/api';
afterEach(()=>cleanup());
const session={id:'conversation',mode:'ask'} as Session;
it('shows the mode beside the composer and changes only the next-turn mode',()=>{
 const onPatch=vi.fn();render(<SessionModePicker session={session} running={false} onPatch={onPatch}/>);
 expect(screen.getByRole('button',{name:'Session mode'})).toHaveTextContent('Ask');fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 fireEvent.click(screen.getByRole('radio',{name:/Agent/}));expect(onPatch).toHaveBeenCalledWith({mode:'agent'});expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
});
it('permits inspection but disables mode changes during active execution',()=>{
 const onPatch=vi.fn();render(<SessionModePicker session={session} running onPatch={onPatch}/>);fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 for(const radio of screen.getAllByRole('radio'))expect(radio).toBeDisabled();expect(onPatch).not.toHaveBeenCalled();
});
it('opens as an anchored popover, not a modal dialog, and closes on Escape, outside click and a second toggle',()=>{
 render(<><button>outside</button><SessionModePicker session={session} running={false} onPatch={vi.fn()}/></>);
 const trigger=screen.getByRole('button',{name:'Session mode'});
 expect(trigger).toHaveAttribute('aria-expanded','false');
 fireEvent.click(trigger);
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 expect(trigger).toHaveAttribute('aria-expanded','true');expect(trigger).toHaveAttribute('aria-controls',screen.getByRole('radiogroup').parentElement!.id);
 fireEvent.keyDown(document,{key:'Escape'});
 expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();expect(trigger).toHaveFocus();
 fireEvent.click(trigger);fireEvent.pointerDown(screen.getByText('outside'));
 expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
 fireEvent.click(trigger);fireEvent.click(trigger);
 expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
});
it('renders mode-specific controls passed through the footer slot and stays accessible',async()=>{
 const {container}=render(<SessionModePicker session={session} running={false} onPatch={vi.fn()} footer={<label>Auto approve<input type="checkbox"/></label>}/>);
 fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 expect(screen.getByLabelText('Auto approve')).toBeInTheDocument();
 await expectNoAxeViolations(container);
});
