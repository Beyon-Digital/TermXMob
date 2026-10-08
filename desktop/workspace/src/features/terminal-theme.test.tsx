import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,waitFor} from '@testing-library/react';
const mock=vi.hoisted(()=>({instances:[] as any[]}));
vi.mock('@xterm/xterm',()=>({Terminal:class{options:any;dispose=vi.fn();reset=vi.fn();constructor(options:any){this.options=options;mock.instances.push(this)}loadAddon(){}open(){}onData(){}attachCustomKeyEventHandler(){}}}));
vi.mock('@xterm/addon-fit',()=>({FitAddon:class{fit(){}}}));
vi.mock('../lib/api',()=>({gql:vi.fn().mockResolvedValue({sessions:[]}),request:vi.fn()}));
import TerminalPanel from './TerminalPanel';
afterEach(()=>{cleanup();vi.unstubAllGlobals();mock.instances.length=0;document.documentElement.removeAttribute('style');delete document.documentElement.dataset.theme});
it('updates the existing terminal appearance without resetting it or replacing its connection',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 const root=document.documentElement;root.style.setProperty('--background','#151716');root.style.setProperty('--foreground','#f0f3ed');root.style.setProperty('--accent','#92d4a3');root.dataset.theme='dark';
 render(<TerminalPanel project={null} onError={vi.fn()}/>);
 const instance=mock.instances[0];expect(instance.options.fontFamily).toContain('Geist Mono');expect(instance.options.theme.background).toBe('#151716');
 root.style.setProperty('--background','#fafaf9');root.style.setProperty('--foreground','#191f1c');root.style.setProperty('--accent','#116c46');root.dataset.theme='light';
 await waitFor(()=>expect(instance.options.theme).toEqual({background:'#fafaf9',foreground:'#191f1c',cursor:'#116c46'}));
 expect(mock.instances).toHaveLength(1);expect(instance.reset).not.toHaveBeenCalled();expect(instance.dispose).not.toHaveBeenCalled();
 cleanup();expect(instance.dispose).toHaveBeenCalledOnce();
});
