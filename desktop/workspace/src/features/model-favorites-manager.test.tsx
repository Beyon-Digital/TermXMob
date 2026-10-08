import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {useModelFavorites,readModelFavorites,toggleModelFavorite} from '../lib/model-favorites';
const api=vi.hoisted(()=>({gql:vi.fn()}));vi.mock('../lib/api',()=>({gql:api.gql}));
import ModelFavoritesManager from './ModelFavoritesManager';
function SessionView({owner}:{owner:string}){const {favorites}=useModelFavorites(owner);return <output aria-label="Session favorites">{JSON.stringify(favorites)}</output>}
afterEach(()=>{cleanup();localStorage.clear();vi.clearAllMocks()});
it('updates the mounted session preference from Manager without changing any session or querying a model',async()=>{
 render(<><ModelFavoritesManager ownerId="alice" engines={[]} accounts={[{id:'account-a',name:'My account',model:'model-a,model-b'}]} search=""/><SessionView owner="alice"/></>);
 fireEvent.click(screen.getByRole('button',{name:'Favorite model-a · My account'}));await waitFor(()=>expect(JSON.parse(screen.getByLabelText('Session favorites').textContent||'[]')).toEqual([JSON.stringify(['internal','account-a','model-a'])]));expect(api.gql).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Remove favorite model-a · My account'}));await waitFor(()=>expect(screen.getByLabelText('Session favorites')).toHaveTextContent('[]'));
});
it('isolates owners and reacts to another window preference without exposing malformed or unbounded rows',async()=>{
 toggleModelFavorite('alice',JSON.stringify(['internal','account-a','model-a']));const view=render(<SessionView owner="alice"/>);expect(screen.getByLabelText('Session favorites')).toHaveTextContent('model-a');view.rerender(<SessionView owner="bob"/>);expect(screen.getByLabelText('Session favorites')).toHaveTextContent('[]');
 localStorage.setItem('termx-model-favorites:bob',JSON.stringify([JSON.stringify(['codex',null,'model-b']),'invalid',JSON.stringify(['codex',null,'x'.repeat(513)])]));fireEvent(window,new StorageEvent('storage',{key:'termx-model-favorites:bob'}));await waitFor(()=>expect(screen.getByLabelText('Session favorites')).toHaveTextContent('model-b'));expect(readModelFavorites('bob')).toHaveLength(1);expect(readModelFavorites('alice')).toHaveLength(1);
});
it('drops a late native catalog when the owning account changes',async()=>{
 let resolve!:(value:{engine_models:string[]})=>void;api.gql.mockReturnValue(new Promise(done=>{resolve=done}));const props={engines:[{id:'codex',label:'Codex',installed:true}],accounts:[],search:''};const view=render(<ModelFavoritesManager {...props} ownerId="alice"/>);fireEvent.click(screen.getByRole('button',{name:'Inspect Codex model catalog'}));view.rerender(<ModelFavoritesManager {...props} ownerId="bob"/>);resolve({engine_models:['late-model']});await waitFor(()=>expect(screen.queryByRole('button',{name:/Favorite late-model/})).not.toBeInTheDocument());
});
