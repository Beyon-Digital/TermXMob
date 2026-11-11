import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, within, waitFor} from '@testing-library/react';
import WorkspaceMenu from './WorkspaceMenu';

afterEach(cleanup);

it.each(['hidden', 'inert', 'aria-hidden'])('closes a portaled menu when its source becomes %s without focusing the inactive trigger', attribute => {
  function Fixture({inactive}: {inactive: boolean}) {
    return <div className="workspace-shell"><div hidden={inactive && attribute === 'hidden'} inert={inactive && attribute === 'inert'} aria-hidden={inactive && attribute === 'aria-hidden' ? true : undefined}>
      <textarea aria-label="Retained editor draft" defaultValue="Unsaved work"/>
      <WorkspaceMenu label="Editor actions"><button type="button">Move editor</button></WorkspaceMenu>
    </div><section aria-label="Other workspace"/></div>;
  }
  const view = render(<Fixture inactive={false}/>);
  const trigger = screen.getByRole('button', {name: 'Editor actions'}), draft = screen.getByRole('textbox', {name: 'Retained editor draft'});
  fireEvent.click(trigger);
  expect(within(screen.getByRole('dialog', {name: 'Editor actions'})).getByRole('button', {name: 'Move editor'})).toHaveFocus();
  view.rerender(<Fixture inactive/>);
  expect(screen.queryByRole('dialog', {name: 'Editor actions'})).not.toBeInTheDocument();
  expect(trigger).not.toHaveFocus(); expect(draft).toHaveValue('Unsaved work');
  view.rerender(<Fixture inactive={false}/>);
  expect(screen.getByRole('textbox', {name: 'Retained editor draft'})).toBe(draft);
  expect(screen.getByRole('button', {name: 'Editor actions'})).toHaveAttribute('aria-expanded', 'false');
});


it('uses arrow and endpoint navigation without activating commands, then restores the trigger on Escape',async()=>{
 const choose=vi.fn();render(<WorkspaceMenu label="Project actions"><button onClick={choose}>Open project</button><button disabled>Unavailable action</button><button onClick={choose}>Project settings</button></WorkspaceMenu>);
 const trigger=screen.getByRole('button',{name:'Project actions'});fireEvent.click(trigger);
 const dialog=screen.getByRole('dialog',{name:'Project actions'}),first=within(dialog).getByRole('button',{name:'Open project'}),last=within(dialog).getByRole('button',{name:'Project settings'});
 expect(first).toHaveFocus();fireEvent.keyDown(first,{key:'ArrowDown'});expect(last).toHaveFocus();
 fireEvent.keyDown(last,{key:'Home'});expect(first).toHaveFocus();fireEvent.keyDown(first,{key:'End'});expect(last).toHaveFocus();expect(choose).not.toHaveBeenCalled();
 fireEvent.keyDown(last,{key:'Escape'});await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());await waitFor(()=>expect(trigger).toHaveFocus());
});

it('leaves arrow keys in an embedded selector and closes on an explicit command',async()=>{
 const choose=vi.fn();render(<WorkspaceMenu label="View actions"><select aria-label="Layout"><option>Chat</option><option>Code</option></select><button data-close-menu onClick={choose}>Open settings</button></WorkspaceMenu>);
 const trigger=screen.getByRole('button',{name:'View actions'});fireEvent.click(trigger);const select=screen.getByRole('combobox',{name:'Layout'});
 select.focus();fireEvent.keyDown(select,{key:'ArrowDown'});expect(select).toHaveFocus();
 fireEvent.click(screen.getByRole('button',{name:'Open settings'}));expect(choose).toHaveBeenCalledOnce();await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('follows the actual trigger when its scroll container moves',()=>{
 let top=120;render(<WorkspaceMenu label="Scroll actions"><button>Open files</button></WorkspaceMenu>);
 const trigger=screen.getByRole('button',{name:'Scroll actions'});trigger.getBoundingClientRect=()=>({x:768,y:top,left:768,right:800,top,bottom:top+32,width:32,height:32,toJSON:()=>({})});
 fireEvent.click(trigger);const dialog=screen.getByRole('dialog',{name:'Scroll actions'});expect(dialog.style.top).toBe('160px');expect(dialog.style.left).toBe('556px');
 top=180;fireEvent.scroll(window);expect(dialog.style.top).toBe('220px');expect(dialog.style.left).toBe('556px');
});
