import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {SignIn} from './App';
import {nativeWorkspace,nativeLogin} from './lib/native';

vi.mock('./lib/native',async original=>({...await original<typeof import('./lib/native')>(),nativeWorkspace:vi.fn(),nativeLogin:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
const methods={configured:false,passcode_required:true,methods:[]};

it('native owner setup submits through the trusted bridge without a host-passcode field',async()=>{
 vi.mocked(nativeWorkspace).mockReturnValue(true);
 vi.mocked(nativeLogin).mockResolvedValue({principal:{id:'owner',display_name:'Owner',scopes:[]},session_id:'native'});
 const done=vi.fn().mockResolvedValue(undefined),error=vi.fn();
 render(<SignIn methods={methods} onDone={done} onError={error} error=""/>);
 expect(screen.queryByLabelText('Host passcode')).not.toBeInTheDocument();
 expect(screen.queryByLabelText('Existing host passcode')).not.toBeInTheDocument();
 const user=userEvent.setup();
 await user.type(screen.getByLabelText('Username'),'native-owner');
 await user.type(screen.getByLabelText(/^Password/),'native-fixture-password-123');
 await user.click(screen.getByRole('button',{name:'Create owner account'}));
 await waitFor(()=>expect(done).toHaveBeenCalledOnce());
 expect(nativeLogin).toHaveBeenCalledWith('native-owner','native-fixture-password-123',true);
 expect(error).not.toHaveBeenCalled();
});

it('browser owner setup still requires the configured host passcode',()=>{
 vi.mocked(nativeWorkspace).mockReturnValue(false);
 render(<SignIn methods={methods} onDone={vi.fn()} onError={vi.fn()} error=""/>);
 expect(screen.getByLabelText('Host passcode')).toBeRequired();
});
