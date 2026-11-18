import {LanguageDescription, type LanguageSupport} from '@codemirror/language';
import type {Extension} from '@codemirror/state';

/* Syntax support is chosen by file name and loaded on demand. @codemirror/language-data is a
   catalogue of about a hundred lazily imported grammars, so neither it nor any grammar is in
   the initial bundle. A path with no matching grammar stays plain text. */

let catalogue: Promise<readonly LanguageDescription[]> | null = null;
const byGrammar = new Map<string, LanguageSupport>();
const byPath = new Map<string, LanguageSupport>();

const fileName = (path: string) => path.split('/').filter(Boolean).at(-1) ?? path;

function languages() {
  catalogue ??= import('@codemirror/language-data').then((module) => module.languages);
  return catalogue;
}

/** The grammar description for a path ("src/app.tsx" gives TSX), or null for plain text. */
export async function resolveLanguage(path: string): Promise<LanguageDescription | null> {
  return LanguageDescription.matchFilename(await languages(), fileName(path));
}

/** Loads and caches the grammar for a path. Resolves null for plain text and for a grammar that fails to load. */
export async function loadLanguage(path: string): Promise<LanguageSupport | null> {
  const description = await resolveLanguage(path);
  if (!description) return null;
  let support = byGrammar.get(description.name);
  if (!support) {
    try {
      support = await description.load();
    } catch {
      return null;
    }
    byGrammar.set(description.name, support);
  }
  byPath.set(path, support);
  return support;
}

/** Synchronous view of what loadLanguage already resolved, so a reopened buffer highlights on its first paint. */
export function cachedLanguage(path: string): Extension {
  return byPath.get(path) ?? [];
}
