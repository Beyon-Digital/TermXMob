import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import SessionCheckoutStatus from './SessionCheckoutStatus';
import type {Session} from '../lib/api';

const base={id:'chat',title:'Review source',mode:'agent',engine:'internal',project_id:'project',cwd:'/source/project',checkout_policy:'auto',checkout_state:'pending'} as Session;
afterEach(cleanup);
it('discloses the committed baseline and retained source edits before the first coding turn',()=>{
 render(<SessionCheckoutStatus session={base}/>);
 expect(screen.getByText('Starts from committed HEAD. Source edits stay in the original folder and are not copied.')).toBeVisible();
 expect(screen.queryByRole('region',{name:'Workspace checkout details'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Workspace checkout details: Automatic checkout'}));
 const details=screen.getByRole('region',{name:'Workspace checkout details'});
 expect(details).toHaveTextContent('Uncommitted and untracked source files stay in the original folder');
 expect(details).toHaveTextContent('Auto becomes available after the host reports a ready isolated checkout');
 expect(within(details).getByText('/source/project')).toBeVisible();
});
it('shows the actual prepared checkout, branch and source-dirty notice without altering the session',()=>{
 const session={...base,checkout_state:'isolated' as const,cwd:'/worktrees/chat',source_cwd:'/source/project',source_dirty:true,worktree_id:'wt',worktree_branch:'termx/chat',worktree_base_ref:'abc123',checkout_notice:'Coding uses a separate worktree from committed HEAD. Source edits were not copied.'};
 const original=structuredClone(session);render(<SessionCheckoutStatus session={session}/>);
 expect(screen.getByText(/Uncommitted source edits were not copied/)).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Workspace checkout details: Isolated checkout'}));
 const details=screen.getByRole('region',{name:'Workspace checkout details'});
 expect(details).toHaveTextContent('/worktrees/chat');expect(details).toHaveTextContent('/source/project');expect(details).toHaveTextContent('termx/chat');expect(details).toHaveTextContent('abc123');expect(session).toEqual(original);
});
it.each([
 ['remote','Runner workspace','This host does not report a local isolated checkout.'],
 ['not-git','Project folder','This folder is not a Git repository.'],
 ['scratch','Scratch workspace','No isolated Git checkout is reported.'],
 ['read-only','Read-only context','without editing project files.'],
])('does not claim isolation for %s context',(state,label,explanation)=>{
 render(<SessionCheckoutStatus session={{...base,checkout_state:state} as Session}/>);
 fireEvent.click(screen.getByRole('button',{name:'Workspace checkout details: '+label}));
 expect(screen.getByRole('region',{name:'Workspace checkout details'})).toHaveTextContent(explanation);
 expect(screen.queryByText('Isolated checkout')).not.toBeInTheDocument();
});
it('keeps a missing checkout visible as an error and offers the existing session recovery route',()=>{
 const onSessionOptions=vi.fn();render(<SessionCheckoutStatus session={{...base,checkout_state:'unavailable',worktree_id:'missing',checkout_notice:'The reported worktree is missing; coding is blocked.'}} onSessionOptions={onSessionOptions}/>);
 expect(screen.getByRole('alert')).toHaveTextContent('coding is blocked');
 fireEvent.click(screen.getByRole('button',{name:'Workspace checkout details: Checkout unavailable'}));
 fireEvent.click(screen.getByRole('button',{name:'Session options'}));expect(onSessionOptions).toHaveBeenCalledOnce();
});

it('describes automatic Plan preparation without promising that no checkout is created',()=>{
 render(<SessionCheckoutStatus session={{...base,mode:'plan'}}/>);
 fireEvent.click(screen.getByRole('button',{name:'Workspace checkout details: Automatic checkout'}));
 expect(screen.getByRole('region',{name:'Workspace checkout details'})).toHaveTextContent('when the first planning or coding turn starts');
 expect(screen.queryByText('Read-only context')).not.toBeInTheDocument();
});
