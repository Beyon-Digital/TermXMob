import {describe,expect,it,vi} from 'vitest';
import {fireEvent,render,screen} from '@testing-library/react';
import TaskHub from './TaskHub';
import type {Session} from '../lib/api';

describe('task dashboard',()=>{
 it('filters recovery decisions and opens the exact selected conversation',()=>{
  const onOpen=vi.fn();
  const sessions=[{id:'blocked',title:'Fix routing',engine:'internal',latest_status:'recovery_confirmation_required',updated_at:10},{id:'active',title:'Build preview',engine:'internal',latest_status:'recovering',updated_at:20}] as Session[];
  render(<TaskHub sessions={sessions} projects={[]} onOpen={onOpen} onNew={()=>{}} onRefresh={()=>{}} loading={false}/>);
  fireEvent.click(screen.getAllByRole('button',{name:'Needs attention'})[0]);
  expect(screen.queryByText('Build preview')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/Fix routing/}));
  expect(onOpen).toHaveBeenCalledWith('blocked');
 });
});
