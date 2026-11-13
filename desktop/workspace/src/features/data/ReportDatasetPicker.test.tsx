import {useState} from 'react';
import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import {ReportDatasetPicker, type ReportDatasetGroup} from './ReportDatasetPicker';

const groups:ReportDatasetGroup[]=[
 {label:'Saved queries',options:[{value:'query:q',label:'Sales query'}]},
 {label:'Views',options:[{value:'view:v',label:'Monthly sales'}]},
 {label:'Tables',options:[{value:'table:orders',label:'Orders'}]},
];
afterEach(cleanup);

function view(onChange=vi.fn()){
 function Controlled(){const [value,setValue]=useState('query:q');return <ReportDatasetPicker groups={groups} value={value} onChange={next=>{setValue(next);onChange(next)}}/>}
 return render(<main><Controlled/></main>);
}

it('opens from the keyboard, names the groups and commits one choice with Enter',async()=>{
 const user=userEvent.setup(),changed=vi.fn();view(changed);
 await user.tab();
 const trigger=screen.getByRole('button',{name:'Data Sales query'});
 expect(trigger).toHaveFocus();expect(screen.getByLabelText('Data')).toBe(trigger);
 await user.keyboard('{Enter}');
 const menu=await screen.findByRole('menu',{name:'Data'});
 expect(within(menu).getByRole('group',{name:'Saved queries'})).toBeInTheDocument();
 expect(within(menu).getByRole('group',{name:'Views'})).toBeInTheDocument();
 expect(within(menu).getByRole('group',{name:'Tables'})).toBeInTheDocument();
 expect(screen.getByRole('menuitemradio',{name:'Sales query'})).toHaveAttribute('aria-checked','true');
 await user.keyboard('{ArrowDown}{Enter}');
 expect(changed).toHaveBeenCalledExactlyOnceWith('view:v');
 expect(screen.queryByRole('menu')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Data Monthly sales'})).toHaveFocus();
 await user.keyboard('{Enter}');
 expect(screen.getAllByRole('menuitemradio',{checked:true})).toHaveLength(1);
 expect(screen.getByRole('menuitemradio',{name:'Monthly sales'})).toHaveAttribute('aria-checked','true');
 await user.keyboard('{Escape}');
});

it('supports typeahead and Escape without changing the selection',async()=>{
 const user=userEvent.setup(),changed=vi.fn();view(changed);
 screen.getByLabelText('Data').focus();
 await user.keyboard('{ArrowDown}');
 await screen.findByRole('menu');
 await user.keyboard('Orders');
 expect(screen.getByRole('menuitemradio',{name:'Orders'})).toHaveFocus();
 await user.keyboard('{Escape}');
 expect(changed).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'Data Sales query'})).toHaveFocus();
});

it('exposes a disabled trigger for busy and empty states',async()=>{
 const user=userEvent.setup(),changed=vi.fn();
 const {rerender}=render(<ReportDatasetPicker groups={groups} value="view:v" disabled onChange={changed}/>);
 expect(screen.getByLabelText('Data')).toBeDisabled();
 await user.click(screen.getByLabelText('Data'));
 expect(screen.queryByRole('menu')).not.toBeInTheDocument();
 rerender(<ReportDatasetPicker groups={[]} value="" onChange={changed}/>);
 expect(screen.getByRole('button',{name:'Data Choose data'})).toBeDisabled();
 expect(changed).not.toHaveBeenCalled();
});

it('has no axe violations when closed or open',async()=>{
 const user=userEvent.setup();view();
 await expectNoAxeViolations(document.body);
 screen.getByLabelText('Data').focus();await user.keyboard('{Enter}');
 await expectNoAxeViolations(await screen.findByRole('menu'));
});
