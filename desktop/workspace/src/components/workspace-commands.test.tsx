import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import WorkspaceCommands from './WorkspaceCommands';
it('filters settings and views by all search terms and runs the keyboard-selected result once',()=>{
 const appearance=vi.fn(),workbench=vi.fn();render(<WorkspaceCommands commands={[{id:'appearance',label:'Appearance',keywords:'settings theme system',run:appearance},{id:'workbench',label:'Workbench',keywords:'view editor files',shortcut:'Mod+2',run:workbench}]}/>);
 const search=screen.getByRole('searchbox',{name:'Search commands'});expect(search).toHaveFocus();
 fireEvent.change(search,{target:{value:'settings system'}});expect(screen.queryByRole('button',{name:/Workbench/})).not.toBeInTheDocument();fireEvent.keyDown(search,{key:'Enter'});expect(appearance).toHaveBeenCalledTimes(1);
 fireEvent.change(search,{target:{value:'editor'}});fireEvent.keyDown(search,{key:'ArrowDown'});expect(screen.getByRole('button',{name:/Workbench/})).toHaveFocus();fireEvent.keyDown(screen.getByRole('button',{name:/Workbench/}),{key:'ArrowUp'});expect(search).toHaveFocus();fireEvent.keyDown(search,{key:'Enter'});expect(workbench).toHaveBeenCalledTimes(1);
 fireEvent.change(search,{target:{value:'no such command'}});expect(screen.getByRole('status')).toHaveTextContent('No matching commands');fireEvent.keyDown(search,{key:'Enter'});expect(workbench).toHaveBeenCalledTimes(1);
});
