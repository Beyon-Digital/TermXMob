import {useState} from 'react';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {afterEach,describe,it,expect} from 'vitest';
import DockWorkspace from './DockWorkspace';
import {layouts,restoreWindowBounds,type DockMap} from '../lib/docking';
afterEach(cleanup);
function Draft(){const [text,setText]=useState('');return <textarea aria-label="Unsent draft" value={text} onChange={event=>setText(event.target.value)}/>}
function Workspace(){const [map,setMap]=useState<DockMap>(layouts.chat);return <DockWorkspace map={map} onChange={setMap} rightVisible rightWidth={380} onDetach={()=>{}} children={{conversation:<Draft/>,editor:<p>Editor buffer</p>,browser:<p>Browser tab</p>}}/>}
describe('workspace docking',()=>{
 it('keeps a real unsent draft mounted when moved between regions and tab groups',()=>{render(<Workspace/>);fireEvent.change(screen.getByLabelText('Unsent draft'),{target:{value:'Keep this work'}});fireEvent.change(screen.getByLabelText('Dock conversation'),{target:{value:'right'}});expect(screen.getByLabelText('Unsent draft')).toHaveValue('Keep this work');fireEvent.change(screen.getByLabelText('Dock editor'),{target:{value:'right'}});fireEvent.click(screen.getAllByRole('button',{name:'Workbench'})[0]);fireEvent.click(screen.getAllByRole('button',{name:'Conversation'})[0]);expect(screen.getByLabelText('Unsent draft')).toHaveValue('Keep this work')});
 it('restores detached geometry when a monitor disappeared',()=>{expect(restoreWindowBounds({x:3000,y:4000,width:1200,height:900},{width:1024,height:768})).toEqual({x:0,y:0,width:1024,height:768})});
});
