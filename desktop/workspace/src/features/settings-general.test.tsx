import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import SettingsGeneral from './SettingsGeneral';
import SettingsSurface from './SettingsSurface';
import {gql} from '../lib/api';

vi.mock('../lib/api',()=>({gql:vi.fn(),request:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.restoreAllMocks()});
beforeEach(()=>{vi.mocked(gql).mockReset();vi.mocked(gql).mockResolvedValue({machine:{hostname:'Worker Mac'}})});
const props={account:'Alex',hostKey:'account-a:host-one',theme:'system' as const,onTheme:vi.fn(),onSelect:vi.fn(),onSignOut:vi.fn(async()=>{})};

it('uses the connected host and exposes direct model, access and theme actions',async()=>{
 render(<SettingsGeneral {...props}/>);
 expect(await screen.findByText('Worker Mac')).toBeInTheDocument();expect(screen.getByText('Alex')).toBeInTheDocument();
 fireEvent.change(screen.getByRole('combobox',{name:'Theme preference'}),{target:{value:'light'}});
 expect(props.onTheme).toHaveBeenCalledWith('light');
 fireEvent.click(screen.getByRole('button',{name:'Manage access'}));expect(props.onSelect).toHaveBeenCalledWith('access');
 fireEvent.click(screen.getByRole('button',{name:'Manage models'}));expect(props.onSelect).toHaveBeenCalledWith('models');
 expect(gql).toHaveBeenCalledOnce();expect(gql).toHaveBeenCalledWith('{ machine { hostname } }');
});

it('keeps host controls available when machine metadata is not permitted',async()=>{
 vi.mocked(gql).mockRejectedValue(new Error('Permission denied'));
 render(<SettingsGeneral {...props}/>);await waitFor(()=>expect(gql).toHaveBeenCalled());
 expect(screen.getByText('Connected host')).toBeInTheDocument();expect(screen.getByRole('button',{name:'Manage access'})).toBeEnabled();
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('does not display a late hostname from an earlier account or host',async()=>{
 let finish!:(value:{machine:{hostname:string}})=>void;
 vi.mocked(gql).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve})).mockResolvedValueOnce({machine:{hostname:'Second host'}});
 const view=render(<SettingsGeneral {...props}/>);
 view.rerender(<SettingsGeneral {...props} account="Jordan" hostKey="account-b:host-two"/>);
 expect(await screen.findByText('Second host')).toBeInTheDocument();
 await act(async()=>finish({machine:{hostname:'Old private host'}}));
 expect(screen.getByText('Jordan')).toBeInTheDocument();expect(screen.queryByText('Old private host')).not.toBeInTheDocument();
 expect(screen.getByText('Second host')).toBeInTheDocument();
});

it('deduplicates sign out and remains recoverable after a failed request',async()=>{
 let fail!:(reason:Error)=>void;
 const logout=vi.fn().mockImplementationOnce(()=>new Promise((_,reject)=>{fail=reject})).mockResolvedValue(undefined);
 render(<SettingsGeneral {...props} onSignOut={logout}/>);
 const button=screen.getByRole('button',{name:'Sign out'});fireEvent.click(button);fireEvent.click(button);
 expect(logout).toHaveBeenCalledOnce();expect(screen.getByRole('button',{name:'Signing out…'})).toBeDisabled();
 await act(async()=>fail(new Error('Host is unavailable')));
 expect(await screen.findByRole('alert')).toHaveTextContent('Host is unavailable');
 const retry=screen.getByRole('button',{name:'Sign out'});expect(retry).toBeEnabled();fireEvent.click(retry);
 await waitFor(()=>expect(logout).toHaveBeenCalledTimes(2));
});

it('does not show an old sign-out failure after the settings identity changes',async()=>{
 let fail!:(reason:Error)=>void;const logout=vi.fn(()=>new Promise<void>((_,reject)=>{fail=reject}));
 const view=render(<SettingsGeneral {...props} onSignOut={logout}/>);
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 view.rerender(<SettingsGeneral {...props} account="Jordan" hostKey="account-b:host-two" onSignOut={logout}/>);
 await act(async()=>fail(new Error('Old session failure')));
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Sign out'})).toBeEnabled();
});

it('keeps immediate theme changes out of the unsaved settings guard',()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<SettingsSurface category="general" onSelect={vi.fn()} onClose={vi.fn()}><SettingsGeneral {...props}/></SettingsSurface>);
 fireEvent.change(screen.getByRole('combobox',{name:'Theme preference'}),{target:{value:'dark'}});
 expect(window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true}))).toBe(true);
 expect(confirm).not.toHaveBeenCalled();expect(props.onTheme).toHaveBeenCalledWith('dark');
});
