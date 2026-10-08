import {fireEvent,render,screen} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import ProjectGroup from './ProjectGroup';
import {orderProjectGroups} from '../lib/project-pins';
it('pins a project without collapsing its group or switching the active session',()=>{const select=vi.fn(),pin=vi.fn();render(<ProjectGroup id="project" name="Build" collapsed={false} pinned={false} busy={false} onSelect={select} onPin={pin}/>);fireEvent.click(screen.getByRole('button',{name:'Pin project Build'}));expect(pin).toHaveBeenCalledOnce();expect(select).not.toHaveBeenCalled();expect(screen.getByRole('button',{name:/^Build$/})).toHaveAttribute('aria-expanded','true')});
it('places pinned projects first while retaining the activity order within both groups',()=>{expect(orderProjectGroups([['recent',1],['pin-b',2],['old',3],['pin-a',4]],['pin-a','pin-b']).map(row=>row[0])).toEqual(['pin-b','pin-a','recent','old'])});
