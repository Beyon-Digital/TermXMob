import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import SessionModePicker from './SessionModePicker';
import {type Session} from '../lib/api';
afterEach(()=>cleanup());
const session={id:'conversation',mode:'ask'} as Session;
it('shows the mode beside the composer and changes only the next-turn mode',()=>{
 const onPatch=vi.fn();render(<SessionModePicker session={session} running={false} onPatch={onPatch}/>);
 expect(screen.getByRole('button',{name:'Session mode'})).toHaveTextContent('Ask');fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 fireEvent.click(screen.getByRole('radio',{name:/Agent/}));expect(onPatch).toHaveBeenCalledWith({mode:'agent'});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('permits inspection but disables mode changes during active execution',()=>{
 const onPatch=vi.fn();render(<SessionModePicker session={session} running onPatch={onPatch}/>);fireEvent.click(screen.getByRole('button',{name:'Session mode'}));
 for(const radio of screen.getAllByRole('radio'))expect(radio).toBeDisabled();expect(onPatch).not.toHaveBeenCalled();
});
