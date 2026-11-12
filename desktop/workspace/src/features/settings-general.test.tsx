import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import SettingsGeneral from './SettingsGeneral';
import SettingsSurface from './SettingsSurface';
import {defaults} from '../lib/keybindings';
import {gql} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';

vi.mock('../lib/api',()=>({gql:vi.fn(),request:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.restoreAllMocks()});
beforeEach(()=>{vi.mocked(gql).mockReset();vi.mocked(gql).mockResolvedValue({machine:{hostname:'Worker Mac'}})});
const props={account:'Alex',theme:'system' as const,onTheme:vi.fn(),bindings:{...defaults},onBindings:vi.fn(),onSignOut:vi.fn(async()=>{})};

it('shows the account, the connected host, appearance and shortcuts on one page with a single page heading',async()=>{
 const {container}=render(<SettingsGeneral {...props}/>);
 expect(await screen.findByText('Worker Mac')).toBeInTheDocument();expect(screen.getByText('Alex')).toBeInTheDocument();
 expect(screen.getAllByRole('heading',{level:2})).toHaveLength(1);expect(screen.getByRole('heading',{level:2})).toHaveTextContent('General');
 for(const name of ['Account','Appearance','Keyboard shortcuts'])expect(screen.getByRole('heading',{level:3,name})).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/Manage access|Change appearance|Manage models/})).toBeNull();
 expect(gql).toHaveBeenCalledOnce();expect(gql).toHaveBeenCalledWith('{ machine { hostname } }');
 await screen.findByRole('button',{name:'Restore default shortcuts'});
 await expectNoAxeViolations(container);
});

it('keeps host controls available when machine metadata is not permitted',async()=>{
 vi.mocked(gql).mockRejectedValue(new Error('Permission denied'));
 render(<SettingsGeneral {...props}/>);await waitFor(()=>expect(gql).toHaveBeenCalled());
 expect(screen.getByText('Connected host')).toBeInTheDocument();expect(screen.getByRole('button',{name:'Sign out'})).toBeEnabled();
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('starts clean for another account or host when the page is remounted with a new key',async()=>{
 let finish!:(value:{machine:{hostname:string}})=>void;
 vi.mocked(gql).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve})).mockResolvedValueOnce({machine:{hostname:'Second host'}});
 const view=render(<SettingsGeneral key="account-a:host-one" {...props}/>);
 view.rerender(<SettingsGeneral key="account-b:host-two" {...props} account="Jordan"/>);
 expect(await screen.findByText('Second host')).toBeInTheDocument();
 await act(async()=>finish({machine:{hostname:'Old private host'}}));
 expect(screen.getByText('Jordan')).toBeInTheDocument();expect(screen.queryByText('Old private host')).not.toBeInTheDocument();
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

it('does not show an old sign-out failure once the page is remounted for a new identity',async()=>{
 let fail!:(reason:Error)=>void;const logout=vi.fn(()=>new Promise<void>((_,reject)=>{fail=reject}));
 const view=render(<SettingsGeneral key="a" {...props} onSignOut={logout}/>);
 fireEvent.click(screen.getByRole('button',{name:'Sign out'}));
 view.rerender(<SettingsGeneral key="b" {...props} account="Jordan" onSignOut={logout}/>);
 await act(async()=>fail(new Error('Old session failure')));
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Sign out'})).toBeEnabled();
});

it('applies a theme change immediately without marking the settings page unsaved',()=>{
 const onTheme=vi.fn();
 render(<SettingsSurface category="preferences" onSelect={vi.fn()} onClose={vi.fn()}><SettingsGeneral {...props} onTheme={onTheme}/></SettingsSurface>);
 fireEvent.change(screen.getByRole('combobox',{name:'Theme preference'}),{target:{value:'dark'}});
 expect(onTheme).toHaveBeenCalledWith('dark');
 expect(document.querySelector('.settings-draft-status')).toBeEmptyDOMElement();
 expect(window.dispatchEvent(new CustomEvent('termx-settings-leave',{cancelable:true,detail:{}}))).toBe(true);
});

it('edits shortcuts in place',async()=>{
 const onBindings=vi.fn();render(<SettingsGeneral {...props} onBindings={onBindings}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Restore default shortcuts'}));
 expect(onBindings).toHaveBeenCalledWith(defaults);
 expect(screen.getByRole('combobox',{name:'Theme preference'})).toHaveValue('system');
});
