import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import ConversationSurfaceHeader from './ConversationSurfaceHeader';
afterEach(cleanup);
it('keeps exact task approval review separate from opening file changes and contextual tools',()=>{
 const open=vi.fn(),review=vi.fn();render(<ConversationSurfaceHeader task={{id:'task',prompt:'Change code',status:'awaiting_approval'}} changes={{files:3,additions:21,deletions:8}} onOpenSurface={open} onReviewTask={review}/>);
 fireEvent.click(screen.getByRole('button',{name:'Review current task'}));expect(review).toHaveBeenCalledOnce();expect(open).not.toHaveBeenCalled();
 for(const [label,surface] of [['Review changes','review'],['Open browser','browser'],['Open terminal','terminal'],['Open editor','editor']]){fireEvent.click(screen.getByRole('button',{name:label}));expect(open).toHaveBeenLastCalledWith(surface)}
 expect(screen.getByRole('status')).toHaveTextContent('Needs approval');expect(screen.getByText('3 files')).toBeVisible();expect(screen.getByText('+21')).toBeVisible();expect(screen.getByText('−8')).toBeVisible();
});
it('does not invent line totals when the checkout summary has no complete counts',()=>{
 render(<ConversationSurfaceHeader changes={{files:2}} onOpenSurface={()=>{}} onReviewTask={()=>{}}/>);expect(screen.getByText('2 files')).toBeVisible();expect(screen.queryByText('+0')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Review current task'})).not.toBeInTheDocument();
});
