import {expect, it} from 'vitest';
import {isManagedFolder, scopeProjects} from './project-labels';

const project = (id: string, path: string) => ({id, path, name: path.split(/[\\/]/).pop() || ''});
const hash = 'a'.repeat(64), scratchId = 'b'.repeat(32), worktreeId = 'c'.repeat(32);

it('recognises host-made scratch and worktree folders but not ordinary projects', () => {
  expect(isManagedFolder(project('s', `/Users/me/.config/termx/scratch/${hash}/${scratchId}`))).toBe(true);
  expect(isManagedFolder(project('w', `/Users/me/.config/termx/worktrees/${worktreeId}`))).toBe(true);
  expect(isManagedFolder(project('w2', `C:\\Users\\me\\termx\\worktrees\\${worktreeId}`))).toBe(true);
  expect(isManagedFolder(project('p', '/Users/me/Documents/GitHub/termxmob'))).toBe(false);
  expect(isManagedFolder(project('p2', '/Users/me/work/worktrees/frontend'))).toBe(false);
});

it('keeps the project already chosen in the scope list', () => {
  const rows = [project('p', '/Users/me/app'), project('w', `/x/worktrees/${worktreeId}`)];
  expect(scopeProjects(rows, '').map(row => row.id)).toEqual(['p']);
  expect(scopeProjects(rows, 'w').map(row => row.id)).toEqual(['p', 'w']);
});
