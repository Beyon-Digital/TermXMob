import {cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {request} from '../lib/api';
import {useCharacterManifest} from './CharacterSprite';
vi.mock('../lib/api',()=>({request:vi.fn()}));
vi.mock('../lib/transfers',()=>({transfer:vi.fn()}));
afterEach(()=>{cleanup();vi.mocked(request).mockReset()});
it('waits for a real selected revision before fetching its manifest',async()=>{
 vi.mocked(request).mockRejectedValue(new Error('Fixture unavailable'));
 const view=renderHook(({revision})=>useCharacterManifest('termx-robot',revision),{initialProps:{revision:0}});
 expect(request).not.toHaveBeenCalled();view.rerender({revision:1});
 await waitFor(()=>expect(view.result.current.error).toBe('Fixture unavailable'));
 expect(request).toHaveBeenCalledExactlyOnceWith('/api/characters/packs/termx-robot/manifest?revision=1');
});
