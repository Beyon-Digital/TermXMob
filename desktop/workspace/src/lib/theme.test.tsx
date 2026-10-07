import {act,renderHook} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {resolveTheme,useTheme} from './theme';
afterEach(()=>vi.restoreAllMocks());
it('tracks live device appearance only in System mode and removes the listener on unmount',()=>{
 let listener:(()=>void)|undefined;const media={matches:false,addEventListener:vi.fn((_type:string,callback:()=>void)=>{listener=callback}),removeEventListener:vi.fn()};vi.spyOn(window,'matchMedia').mockReturnValue(media as unknown as MediaQueryList);
 const hook=renderHook(()=>useTheme());expect(document.documentElement.dataset.theme).toBe('dark');
 act(()=>hook.result.current[1]('system'));expect(document.documentElement.dataset.theme).toBe('light');expect(document.documentElement.dataset.themePreference).toBe('system');
 act(()=>{media.matches=true;listener?.()});expect(document.documentElement.dataset.theme).toBe('dark');
 act(()=>hook.result.current[1]('light'));act(()=>{media.matches=true;listener?.()});expect(document.documentElement.dataset.theme).toBe('light');
 hook.unmount();expect(media.removeEventListener).toHaveBeenCalledWith('change',expect.any(Function));expect(resolveTheme('system',false)).toBe('light');
});
