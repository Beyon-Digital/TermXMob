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
 fireEvent.click(screen.getByRole('radio',{name:/Agent/}));expect(onPatch).toHaveBeenCalledWith({mode:'agent'});
 // The popover stays open so the Auto switch that Agent mode adds can be reached; Escape closes it.
 expect(screen.getByRole('radiogroup')).toBeInTheDocument();
 fireEvent.keyDown(document,{key:'Escape'});expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Session mode'})).toHaveFocus();
});
it('keeps the popover open when the mode changes from the keyboard',()=>{
 const onPatch=vi.fn();render(<SessionModePicker session={session} running={false} onPatch={onPatch}/>);fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 const plan=screen.getByRole('radio',{name:/Plan/});plan.focus();fireEvent.click(plan);
 expect(onPatch).toHaveBeenCalledWith({mode:'plan'});expect(screen.getByRole('radiogroup')).toBeInTheDocument();expect(plan).toHaveFocus();
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
it('focuses the checked mode, not the first one, when the popover opens', () => {
 render(<SessionModePicker session={{id:'conversation',mode:'agent'} as Session} running={false} onPatch={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 expect(screen.getByRole('radio',{name:/Agent/})).toHaveFocus();
});
it('focuses the first available mode when the checked one cannot be focused',()=>{
 render(<SessionModePicker session={{id:'conversation',mode:'plan',context:{}} as unknown as Session} running={false} onPatch={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 const focused=document.activeElement as HTMLInputElement;
 expect(focused.type).toBe('radio');expect(focused.disabled).toBe(false);
});
it('opens on the checked mode when the keyboard opens the popover',()=>{
 render(<SessionModePicker session={{id:'conversation',mode:'agent'} as Session} running={false} onPatch={vi.fn()}/>);
 const trigger=screen.getByRole('button',{name:'Session mode'});trigger.focus();fireEvent.keyDown(trigger,{key:'Enter'});fireEvent.click(trigger);
 expect(screen.getByRole('radio',{name:/Agent/})).toHaveFocus();
});
it('renders the options outside the clipped composer so they can be clicked',()=>{
 render(<div className="composer" style={{overflow:'hidden'}}><SessionModePicker session={session} running={false} onPatch={vi.fn()}/></div>);
 fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 const group=screen.getByRole('radiogroup');
 expect(group.closest('.composer')).toBeNull();
 expect(group.closest('.mode-popover')).not.toBeNull();
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
 it('shows the Auto switch only in Agent mode and reports changes without run quotas',()=>{
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
  expect(toggle).toHaveAccessibleDescription(/No ready checkout has been reported/);
 });
 it('keeps Auto approval behavior without quota fields or inherited numeric ceilings',()=>{
  const onAuto=vi.fn();render(<SessionModePicker session={agent} running={false} onPatch={vi.fn()} auto={{enabled:true,ceilings:{tool_calls:1,steps:1,seconds:1,tokens:1,cost_usd:1}}} onAutoChange={onAuto}/>);open();
  expect(screen.queryByRole('button',{name:'Auto limits'})).not.toBeInTheDocument();expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch',{name:'Auto'}));expect(onAuto).toHaveBeenCalledWith({enabled:false,ceilings:AUTO_CEILING_DEFAULTS});
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

it('suspends its portal without moving focus or changing approval mode while Agent is hidden',()=>{
 const agent={id:'conversation',mode:'agent',engine:'internal',worktree_id:'wt'} as Session;
 const onPatch=vi.fn(),onAutoChange=vi.fn(),auto={...AUTO_OFF,enabled:true};
 const surface=(visible:boolean)=><><button>Studio action</button><SessionModePicker session={agent} running={false} onPatch={onPatch} auto={auto} onAutoChange={onAutoChange} visible={visible}/></>;
 const view=render(surface(true));fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 const toggle=screen.getByRole('switch',{name:'Auto'}),popup=toggle.closest('.mode-popover')!;toggle.focus();
 view.rerender(surface(false));expect(popup).toHaveAttribute('hidden');expect(popup).toHaveAttribute('inert');expect(popup.contains(document.activeElement)).toBe(false);
 const studio=screen.getByRole('button',{name:'Studio action'});studio.focus();fireEvent.keyDown(document,{key:'Escape'});fireEvent.pointerDown(studio);
 expect(studio).toHaveFocus();expect(onAutoChange).not.toHaveBeenCalled();expect(onPatch).not.toHaveBeenCalled();
 view.rerender(surface(true));expect(screen.getByRole('switch',{name:'Auto'})).toBe(toggle);expect(toggle).toBeChecked();expect(onAutoChange).not.toHaveBeenCalled();
});

it('keeps Auto disabled while an automatic checkout is pending and explains that no manual checkout step is needed',()=>{
 const session={id:'pending',mode:'agent',engine:'internal',project_id:'project',checkout_policy:'auto',checkout_state:'pending',worktree_id:null} as Session;
 render(<SessionModePicker session={session} running={false} onPatch={vi.fn()} auto={AUTO_OFF} onAutoChange={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 const toggle=screen.getByRole('switch',{name:'Auto'});expect(toggle).toBeDisabled();expect(toggle).toHaveAccessibleDescription(/created automatically.*planning or coding turn.*checkout is ready/);
});
