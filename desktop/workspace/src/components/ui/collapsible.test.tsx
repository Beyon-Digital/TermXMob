import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {Collapsible,CollapsibleContent,CollapsibleTrigger} from './collapsible';
afterEach(cleanup);
it('preserves keyboard disclosure semantics and makes force-mounted closed controls inert',async()=>{
 render(<Collapsible><CollapsibleTrigger>Task details</CollapsibleTrigger><CollapsibleContent forceMount><button>Inspect output</button></CollapsibleContent></Collapsible>);
 const toggle=screen.getByRole('button',{name:'Task details'}),content=screen.getByRole('button',{name:'Inspect output',hidden:true}).parentElement!;
 expect(toggle).toHaveAttribute('aria-expanded','false');expect(content).toHaveAttribute('inert');
 fireEvent.click(toggle);await waitFor(()=>expect(content).not.toHaveAttribute('inert'));expect(toggle).toHaveAttribute('aria-expanded','true');
 act(()=>screen.getByRole('button',{name:'Inspect output'}).focus());fireEvent.click(toggle);
 await waitFor(()=>expect(content).toHaveAttribute('inert'));expect(toggle).toHaveFocus();
});
it('supports controlled state and custom classes without adding layout wrappers',async()=>{
 const view=render(<Collapsible open><CollapsibleTrigger className="activity-label">Activity</CollapsibleTrigger><CollapsibleContent className="result-details" forceMount>Real output</CollapsibleContent></Collapsible>);
 expect(screen.getByRole('button',{name:'Activity'})).toHaveClass('activity-label','ui-disclosure-trigger');
 expect(screen.getByText('Real output')).toHaveClass('result-details','ui-disclosure-content');
 view.rerender(<Collapsible open={false}><CollapsibleTrigger>Activity</CollapsibleTrigger><CollapsibleContent forceMount>Real output</CollapsibleContent></Collapsible>);
 await waitFor(()=>expect(screen.getByText('Real output')).toHaveAttribute('inert'));
});
