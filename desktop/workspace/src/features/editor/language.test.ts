import {describe, expect, it} from 'vitest';
import {cachedLanguage, loadLanguage, resolveLanguage} from './language';

describe('language by file extension', () => {
  it.each([
    ['src/app.ts', 'TypeScript'],
    ['src/App.tsx', 'TSX'],
    ['src/app.jsx', 'JSX'],
    ['tool.py', 'Python'],
    ['package.json', 'JSON'],
    ['README.md', 'Markdown'],
    ['styles/site.css', 'CSS'],
    ['index.html', 'HTML'],
    ['src/main.rs', 'Rust'],
    ['cmd/server.go', 'Go'],
    ['ci/workflow.yaml', 'YAML'],
    ['ci/workflow.yml', 'YAML'],
  ])('%s is %s', async (path, name) => {
    expect((await resolveLanguage(path))?.name).toBe(name);
  });

  it('keeps unknown extensions as plain text', async () => {
    for (const path of ['notes.unknownext', 'LICENSE', 'data.bin', '']) {
      expect(await resolveLanguage(path)).toBeNull();
      expect(await loadLanguage(path)).toBeNull();
    }
  });

  it('no longer treats everything that is not python as JavaScript', async () => {
    expect((await resolveLanguage('main.rs'))?.name).not.toBe('JavaScript');
    expect((await resolveLanguage('notes.unknownext'))?.name ?? null).not.toBe('JavaScript');
  });

  it('loads a grammar once and serves it synchronously afterwards', async () => {
    expect(cachedLanguage('lib/a.ts')).toEqual([]);
    const first = await loadLanguage('lib/a.ts');
    expect(first?.language.name).toBe('typescript');
    expect(cachedLanguage('lib/a.ts')).toBe(first);
    expect(await loadLanguage('lib/b.ts')).toBe(first);
  });
});
