import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import SettingsGeneral from './SettingsGeneral';
import {gql} from '../lib/api';
vi.mock('../lib/api',()=>({gql:vi.fn()}));
afterEach(cleanup);
beforeEach(()=>{vi.mocked(gql).mockReset();vi.mocked(gql).mockResolvedValue({machine:{hostname:'Worker Mac'}})});
const props={account:'Alex',theme:'system' as const,onTheme:vi.fn(),onSelect:vi.fn(),onSignOut:vi.fn(async()=>{})};
it('uses the connected host and offers direct preference and access actions',async()=>{
 render(<SettingsGeneral {...props}/>);
 expect(await screen.findByText('Worker Mac')).toBeInTheDocument();
 expect(screen.getByText('Alex')).toBeInTheDocument();
 expect(screen.queryByLabelText('Settings scope')).not.toBeInTheDocument();
 fireEvent.change(screen.getByRole('combobox',{name:'Theme preference'}),{target:{value:'light'}});
 expect(props.onTheme).toHaveBeenCalledWith('light');
 fireEvent.click(screen.getByRole('button',{name:'Manage access'}));expect(props.onSelect).toHaveBeenCalledWith('access');
 fireEvent.click(screen.getByRole('button',{name:'Manage models'}));expect(props.onSelect).toHaveBeenCalledWith('models');
});
it('keeps sign out recoverable after a failed request',async()=>{
 const logout=vi.fn().mockRejectedValue(new Error('Host is unavailable'));
 render(<SettingsGeneral {...props} onSignOut={logout}/>);
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Host is unavailable');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Sign out'})).toBeEnabled());
});
it('keeps host controls available when machine metadata is not permitted',async()=>{
 vi.mocked(gql).mockRejectedValue(new Error('Permission denied'));
 render(<SettingsGeneral {...props}/>);
 await waitFor(()=>expect(gql).toHaveBeenCalled());
 expect(screen.getByText('Connected host')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Manage access'})).toBeEnabled();
});
