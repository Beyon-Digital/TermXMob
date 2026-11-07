import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {expectNoAxeViolations} from '../test-axe';
import PlanReviewCard,{isRevisablePlan} from './PlanReviewCard';
import {resolvePlan} from '../lib/agentic';

vi.mock('../lib/agentic',async importOriginal=>({...await importOriginal<typeof import('../lib/agentic')>(),resolvePlan:vi.fn(async()=>undefined)}));
const approval={id:'approval-1',status:'pending',kind:'plan',payload:{title:'Approve this plan',plan:{summary:'Add a retry button',markdown:'## Plan\n\n1. Read `src/app.ts`\n2. Add the button\n\n<script>window.hacked=1</script>'}}};
const props={taskId:'task-1',approval,onDone:vi.fn(),onError:vi.fn()};
beforeEach(()=>{vi.mocked(resolvePlan).mockReset();vi.mocked(resolvePlan).mockResolvedValue(undefined);props.onDone.mockReset();props.onError.mockReset()});
afterEach(cleanup);

describe('PlanReviewCard',()=>{
 it('renders the plan markdown as text and never runs embedded script',async()=>{
  const {container}=render(<PlanReviewCard {...props}/>);
  expect(await screen.findByRole('heading',{name:'Plan'})).toBeInTheDocument();expect(screen.getByText('Add the button')).toBeInTheDocument();
  expect(container.querySelector('script')).toBeNull();expect((window as unknown as {hacked?:number}).hacked).toBeUndefined();
 });
 it('approves with the exact approval and reports completion',async()=>{
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Approve plan'}));
  await waitFor(()=>expect(props.onDone).toHaveBeenCalledOnce());expect(resolvePlan).toHaveBeenCalledWith('task-1','approval-1','approved',undefined);
  expect(screen.getByText(/Plan approved/)).toBeInTheDocument();
 });
 it('opens an inline textarea for Request changes and sends revise with the feedback',async()=>{
  render(<PlanReviewCard {...props}/>);expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Request changes'}));
  const box=screen.getByRole('textbox',{name:'What should change?'});expect(box).toHaveFocus();
  fireEvent.change(box,{target:{value:'  Skip the migration  '}});fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
  await waitFor(()=>expect(props.onDone).toHaveBeenCalledOnce());expect(resolvePlan).toHaveBeenCalledWith('task-1','approval-1','revise','Skip the migration');
  expect(screen.getByText(/Feedback sent/)).toBeInTheDocument();
 });
 it('asks for feedback instead of sending an empty revision',()=>{
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Request changes'}));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
  expect(resolvePlan).not.toHaveBeenCalled();expect(screen.getByText('Say what should change, or approve the plan as it is.')).toBeInTheDocument();
  expect(screen.getByRole('textbox',{name:'What should change?'})).toHaveAttribute('aria-invalid','true');
 });
 it('can go back to reviewing without sending',()=>{
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Request changes'}));fireEvent.click(screen.getByRole('button',{name:'Keep reviewing'}));
  expect(screen.queryByRole('textbox')).toBeNull();expect(screen.getByRole('button',{name:'Request changes'})).toHaveFocus();
 });
 it('keeps the feedback and shows the host message when the decision fails',async()=>{
  vi.mocked(resolvePlan).mockRejectedValueOnce(new Error('This plan was already decided'));
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Request changes'}));
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Use the existing hook'}});fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
  expect(await screen.findByText('This plan was already decided')).toBeInTheDocument();expect(screen.getByRole('textbox')).toHaveValue('Use the existing hook');
  expect(props.onError).toHaveBeenCalledOnce();expect(props.onDone).not.toHaveBeenCalled();expect(screen.getByRole('button',{name:'Send feedback'})).not.toBeDisabled();
 });
 it('sends one decision for a double click and ignores a late reply after the approval changes',async()=>{
  let finish!:()=>void;vi.mocked(resolvePlan).mockImplementationOnce(()=>new Promise<void>(resolve=>{finish=resolve}));
  const view=render(<PlanReviewCard {...props}/>);const approve=screen.getByRole('button',{name:'Approve plan'});fireEvent.click(approve);fireEvent.click(approve);
  expect(resolvePlan).toHaveBeenCalledOnce();
  view.rerender(<PlanReviewCard key="approval-2" {...props} approval={{...approval,id:'approval-2'}}/>);finish();
  await waitFor(()=>expect(screen.getByRole('button',{name:'Approve plan'})).not.toBeDisabled());expect(props.onDone).not.toHaveBeenCalled();
 });
 it('keeps the feedback form closed to a second send once the feedback went through',async()=>{
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Request changes'}));
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Use the hook'}});fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));
  await waitFor(()=>expect(props.onDone).toHaveBeenCalledOnce());
  expect(screen.getByRole('button',{name:'Send feedback'})).toBeDisabled();expect(screen.getByRole('button',{name:'Keep reviewing'})).toBeDisabled();expect(screen.getByRole('textbox')).toBeDisabled();
  fireEvent.submit(screen.getByRole('textbox').closest('form')!);expect(resolvePlan).toHaveBeenCalledOnce();
 });
 it('names the pending action on the button while the host answers',async()=>{
  let finish!:()=>void;vi.mocked(resolvePlan).mockImplementationOnce(()=>new Promise<void>(resolve=>{finish=resolve}));
  render(<PlanReviewCard {...props}/>);fireEvent.click(screen.getByRole('button',{name:'Approve plan'}));
  expect(screen.getByRole('button',{name:'Approving…'})).toBeDisabled();finish();await waitFor(()=>expect(props.onDone).toHaveBeenCalledOnce());
 });
 it('is locked while the workspace is locked',()=>{
  render(<PlanReviewCard {...props}/>);act(()=>{window.dispatchEvent(new Event('termx-locked'))});
  expect(screen.getByRole('button',{name:'Approve plan'})).toBeDisabled();
 });
 it('has no axe violations in review, feedback and error states',async()=>{
  vi.mocked(resolvePlan).mockRejectedValueOnce(new Error('Nope'));
  const {container}=render(<PlanReviewCard {...props}/>);await screen.findByRole('heading',{name:'Plan'});await expectNoAxeViolations(container);
  fireEvent.click(screen.getByRole('button',{name:'Request changes'}));fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await expectNoAxeViolations(container);
  fireEvent.change(screen.getByRole('textbox'),{target:{value:'Change it'}});fireEvent.click(screen.getByRole('button',{name:'Send feedback'}));await screen.findByText('Nope');await expectNoAxeViolations(container);
 });
});
describe('isRevisablePlan',()=>{
 it('only takes plans that carry markdown',()=>{
  expect(isRevisablePlan(approval)).toBe(true);
  expect(isRevisablePlan({...approval,kind:'tool'})).toBe(false);
  expect(isRevisablePlan({...approval,payload:{plan:{summary:'x'}}})).toBe(false);
 });
});
