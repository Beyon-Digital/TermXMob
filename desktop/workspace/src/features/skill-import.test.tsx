import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import ExtensionManager from './ExtensionManager';
import {request} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';

vi.mock('../lib/api', () => ({request: vi.fn()}));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => { mocked.mockReset(); });

const preview = {
  id: 'prev1', digest: 'd'.repeat(64), bundle: {id: 'my-skill', version: '1', files: {}}, permission_diff: {added: ['files-read'], removed: []},
  dependencies: [], provenance: {kind: 'folder-import'},
  import: {skills: [{id: 'my-skill', name: 'My skill', files: [{path: 'a'}, {path: 'b'}]}], skipped: [{path: 'logo.png', reason: 'binary'}], bytes: 10},
};

function picked(entries: Record<string, string | Uint8Array>) {
  return Object.entries(entries).map(([path, content]) => {
    const file = new File([content as BlobPart], path.split('/').pop()!);
    Object.defineProperty(file, 'webkitRelativePath', {value: path});
    return file;
  });
}

function setup(sources: Row[] = []) {
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.endsWith('/import-preview')) return preview as any;
    if (path.includes('/detected')) return {sources} as any;
    if (path.includes('/compatibility')) return {sources: []} as any;
    if (path.includes('/skill-sources')) return {sources: [], roots: [], diagnostics: []} as any;
    return {} as any;
  });
  return render(<ExtensionManager data={{}} projectId={null} onChanged={vi.fn()} />);
}
type Row = Record<string, any>;
const importCalls = () => mocked.mock.calls.filter(([path]) => String(path).endsWith('/import-preview'));

describe('skill folder import', () => {
  it('posts a picked folder as a files map and shows what was skipped before anything is installed', async () => {
    setup();
    fireEvent.click(await screen.findByText('Import a skill folder'));
    const input = screen.getByLabelText('Skill folder') as HTMLInputElement;
    const files = picked({
      'my-skill/SKILL.md': '---\nname: My skill\ndescription: Does a thing\n---\nBody',
      'my-skill/references/guide.md': 'Guide',
      'my-skill/logo.png': new Uint8Array([137, 80, 0, 1]),
      'my-skill/.git/config': 'hidden',
      'my-skill/node_modules/x/index.js': 'dependency',
    });
    fireEvent.change(input, {target: {files}});
    await waitFor(() => expect(importCalls()).toHaveLength(1));
    const body = JSON.parse(String(importCalls()[0][1]?.body));
    expect(body).toMatchObject({name: 'my-skill', version: '1'});
    expect([...body.skipped].sort()).toEqual(['.git', 'logo.png', 'node_modules']);
    expect(Object.keys(body.files).sort()).toEqual(['SKILL.md', 'references/guide.md']);
    expect(await screen.findByText('Review my-skill · 1')).toBeInTheDocument();
    expect(screen.getByText(/not run by TermX/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('1 skipped'));
    expect(screen.getByText('logo.png · binary')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Install reviewed version'})).toBeEnabled();
    expect(mocked.mock.calls.some(([path]) => String(path).includes('/install'))).toBe(false);
  });

  it('refuses an oversize file before posting and says what to do', async () => {
    setup();
    fireEvent.click(await screen.findByText('Import a skill folder'));
    const big = picked({'my-skill/SKILL.md': 'x'.repeat(512_001)});
    fireEvent.change(screen.getByLabelText('Skill folder'), {target: {files: big}});
    expect(await screen.findByRole('alert')).toHaveTextContent('SKILL.md is larger than 512 KB');
    expect(importCalls()).toHaveLength(0);
  });

  it('lists detected sources by name only and reviews one by token', async () => {
    setup([{token: 'a'.repeat(64), folder: 'legacy', name: 'Legacy skill', description: 'Old helper', source: 'compat:claude', project_id: null}]);
    fireEvent.click(await screen.findByText('Import a skill folder'));
    fireEvent.click(screen.getByRole('button', {name: 'Find skills on this computer'}));
    fireEvent.click(await screen.findByRole('button', {name: 'Review import of Legacy skill'}));
    await waitFor(() => expect(importCalls()).toHaveLength(1));
    expect(JSON.parse(String(importCalls()[0][1]?.body))).toEqual({version: '1', token: 'a'.repeat(64), project_id: null});
  });

  it('says what to do when no skills are detected', async () => {
    setup([]);
    fireEvent.click(await screen.findByText('Import a skill folder'));
    fireEvent.click(screen.getByRole('button', {name: 'Find skills on this computer'}));
    expect(await screen.findByText(/Pick a folder instead/)).toBeInTheDocument();
  });

  it('has no accessibility violations when open with a review and detected sources', async () => {
    const {container} = setup([{token: 'b'.repeat(64), folder: 'x', name: 'X skill', description: '', source: 'user', project_id: null}]);
    fireEvent.click(await screen.findByText('Import a skill folder'));
    fireEvent.click(screen.getByRole('button', {name: 'Find skills on this computer'}));
    await screen.findByRole('button', {name: 'Review import of X skill'});
    fireEvent.change(screen.getByLabelText('Skill folder'), {target: {files: picked({'my-skill/SKILL.md': 'Body'})}});
    await screen.findByText('Review my-skill · 1');
    await expectNoAxeViolations(container);
  });
});
