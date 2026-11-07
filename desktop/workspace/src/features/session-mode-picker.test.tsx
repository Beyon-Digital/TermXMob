import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import SessionModePicker from './SessionModePicker';
import {type Session} from '../lib/api';
import {AUTO_CEILING_DEFAULTS,AUTO_OFF} from '../lib/agentic';
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
it('stays accessible with the mode list open',async()=>{
 const {container}=render(<SessionModePicker session={session} running={false} onPatch={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 await expectNoAxeViolations(container);
});
describe('Plan and Auto',()=>{
 const agent={id:'conversation',mode:'agent',engine:'internal',worktree_id:'wt'} as Session;
 const open=()=>fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 it('offers Ask, Plan and Agent and patches the chosen mode',()=>{
  const onPatch=vi.fn();render(<SessionModePicker session={session} running={false} onPatch={onPatch}/>);open();
  expect(screen.getAllByRole('radio').map(radio=>(radio as HTMLInputElement).value)).toEqual(['ask','plan','agent']);
  fireEvent.click(screen.getByRole('radio',{name:/Plan/}));expect(onPatch).toHaveBeenCalledWith({mode:'plan'});
 });
 it('disables Plan with a reason for engines that plan on their own',()=>{
  render(<SessionModePicker session={{...session,engine:'claude'} as Session} running={false} onPatch={vi.fn()}/>);open();
  expect(screen.getByRole('radio',{name:/Plan/})).toBeDisabled();expect(screen.getByText(/plans on its own/)).toBeInTheDocument();
 });
 it('shows the Auto switch only in Agent mode and reports changes with the default limits',()=>{
  const onAuto=vi.fn();const view=render(<SessionModePicker session={session} running={false} onPatch={vi.fn()} auto={AUTO_OFF} onAutoChange={onAuto}/>);open();
  expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  view.rerender(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={AUTO_OFF} onAutoChange={onAuto}/>);
  const toggle=screen.getByRole('switch',{name:'Auto'});expect(toggle).not.toBeChecked();fireEvent.click(toggle);
  expect(onAuto).toHaveBeenCalledWith({enabled:true,ceilings:AUTO_CEILING_DEFAULTS});
 });
 it('tints the chip and names Auto when it is on',()=>{
  render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={vi.fn()}/>);
  const chip=screen.getByRole('button',{name:'Session mode'});expect(chip).toHaveTextContent('Agent · Auto');expect(chip).toHaveClass('is-auto');
 });
 it('disables Auto with the reason when the task is not in a worktree',()=>{
  render(<SessionModePicker session={{...agent,worktree_id:null} as Session} running={false} onPatch={vi.fn()} auto={AUTO_OFF} onAutoChange={vi.fn()}/>);open();
  const toggle=screen.getByRole('switch',{name:'Auto'});expect(toggle).toBeDisabled();
  expect(toggle).toHaveAccessibleDescription(/Choose a worktree in Session options/);
 });
 it('edits the five limits, prefilled with host defaults, and reports them',async()=>{
  const onAuto=vi.fn();const {container}=render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={onAuto}/>);open();
  const limits=screen.getByRole('button',{name:'Auto limits'});expect(limits).toHaveAttribute('aria-expanded','false');fireEvent.click(limits);
  expect(screen.getAllByRole('spinbutton').map(input=>(input as HTMLInputElement).value)).toEqual(['100','50','1800','500000','5']);
  const calls=screen.getByRole('spinbutton',{name:'Tool calls'});
  fireEvent.change(calls,{target:{value:'2'}});fireEvent.change(calls,{target:{value:'20'}});expect(onAuto).not.toHaveBeenCalled();
  fireEvent.blur(calls);expect(onAuto).toHaveBeenCalledOnce();
  expect(onAuto).toHaveBeenLastCalledWith({enabled:true,ceilings:{...AUTO_CEILING_DEFAULTS,tool_calls:20}});
  await expectNoAxeViolations(container);
 });
 it('rejects an empty or non-positive limit without sending it',()=>{
  const onAuto=vi.fn();render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={onAuto}/>);open();fireEvent.click(screen.getByRole('button',{name:'Auto limits'}));
  const steps=screen.getByRole('spinbutton',{name:'Steps'});fireEvent.change(steps,{target:{value:''}});
  fireEvent.blur(steps);
  expect(onAuto).not.toHaveBeenCalled();expect(steps).toHaveAttribute('aria-invalid','true');expect(screen.getByText('Enter a whole number above zero.')).toBeInTheDocument();
  fireEvent.change(steps,{target:{value:'0'}});fireEvent.keyDown(steps,{key:'Enter'});expect(onAuto).not.toHaveBeenCalled();
 });
 it('commits a limit on Enter and shows the held value again once it is sent',()=>{
  const onAuto=vi.fn();render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={onAuto}/>);open();fireEvent.click(screen.getByRole('button',{name:'Auto limits'}));
  const steps=screen.getByRole('spinbutton',{name:'Steps'});fireEvent.change(steps,{target:{value:'10'}});fireEvent.keyDown(steps,{key:'Enter'});
  expect(onAuto).toHaveBeenCalledWith({enabled:true,ceilings:{...AUTO_CEILING_DEFAULTS,steps:10}});expect(steps).toHaveValue(50);
 });
 it('does not name Auto on the chip when the conversation cannot run it',()=>{
  render(<SessionModePicker session={{...agent,worktree_id:null} as Session} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={vi.fn()}/>);
  const chip=screen.getByRole('button',{name:'Session mode'});expect(chip).not.toHaveTextContent('Auto');expect(chip).not.toHaveClass('is-auto');
 });
 it('stays accessible with Auto on and keeps Escape returning focus to the chip',async()=>{
  const {container}=render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{...AUTO_OFF,enabled:true}} onAutoChange={vi.fn()}/>);open();
  await expectNoAxeViolations(container);fireEvent.keyDown(document,{key:'Escape'});expect(screen.getByRole('button',{name:'Session mode'})).toHaveFocus();
 });
});
