import {describe,it,expect} from 'vitest';
import {defaults,strokeMatches,bindingWarnings,loadBindings,protectedSurface,remoteInputSurface,terminalSurface,shortcutSurface} from './keybindings';
describe('workspace keyboard contracts',()=>{
 it('matches both platforms and chord steps without capturing unrelated control keys',()=>{expect(strokeMatches('Mod+K',new KeyboardEvent('keydown',{key:'k',metaKey:true}))).toBe(true);expect(strokeMatches('Mod+K',new KeyboardEvent('keydown',{key:'k',ctrlKey:true}))).toBe(true);expect(strokeMatches('Z',new KeyboardEvent('keydown',{key:'z'}))).toBe(true);expect(strokeMatches('Mod+B',new KeyboardEvent('keydown',{key:'b',ctrlKey:true,altKey:true}))).toBe(false)});
 it('warns for ambiguous commands and browser-reserved shortcuts and restores malformed state',()=>{expect(bindingWarnings({...defaults,chat:'Mod+W',workbench:'Mod+W'}).filter(warning=>/^(Chat|Workbench)/.test(warning))).toHaveLength(3);expect(loadBindings('{invalid')).toEqual(defaults);expect(loadBindings('{"chat":"Mod+9"}').chat).toBe('Mod+9')});
 it('preserves terminal and controlled computer input',()=>{const computer=document.createElement('div');computer.className='computer-surface';const input=document.createElement('input');computer.append(input);expect(protectedSurface(input)).toBe(true);expect(protectedSurface(document.createElement('textarea'))).toBe(false);const remote=document.createElement('textarea');remote.className='browser-remote-input';expect(protectedSurface(remote)).toBe(true);expect(strokeMatches(defaults.browserNew,new KeyboardEvent('keydown',{key:'t',ctrlKey:true,altKey:true}))).toBe(true);expect(defaults.browserClose).toBe('Mod+Alt+W')});
});
describe('where a key press landed',()=>{
 it('separates remote input, terminals and the rest of the page',()=>{
  const terminal=document.createElement('div');terminal.className='xterm';const typed=document.createElement('textarea');terminal.append(typed);
  const remote=document.createElement('div');remote.className='browser-frame';
  expect(shortcutSurface(typed)).toBe('terminal');expect(shortcutSurface(remote)).toBe('remote');expect(shortcutSurface(document.createElement('textarea'))).toBe('page');expect(shortcutSurface(null)).toBe('page');
  expect(protectedSurface(typed)).toBe(true);expect(remoteInputSurface(typed)).toBe(false);expect(terminalSurface(typed)).toBe(true);
 });
});
