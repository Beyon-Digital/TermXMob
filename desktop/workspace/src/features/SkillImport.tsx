import {useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';

type Row = Record<string, any>;
type Request = <T = unknown>(path: string, init?: RequestInit) => Promise<T>;

const MAX_FILES = 400;
const MAX_FILE_BYTES = 512_000;
const SKIPPED_FOLDERS = new Set(['node_modules', '__pycache__']);
const post = (value: unknown): RequestInit => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});

/** Reads a browser-picked folder as relative path to text. Binary files are only listed; hidden and dependency entries are listed by name and never read. The server decides what to refuse. */
export async function readPickedFolder(list: FileList): Promise<{name: string; files: Record<string, string>; skipped: string[]}> {
  const files: Record<string, string> = {};
  const skipped = new Set<string>();
  let name = '';
  for (const file of Array.from(list)) {
    const parts = (file.webkitRelativePath || file.name).split('/');
    name = name || (parts.length > 1 ? parts[0] : '');
    const relative = parts.length > 1 ? parts.slice(1) : parts;
    const hidden = relative.findIndex(part => part.startsWith('.') || SKIPPED_FOLDERS.has(part));
    if (hidden >= 0) { skipped.add(relative.slice(0, hidden + 1).join('/')); continue; }
    if (Object.keys(files).length + skipped.size >= MAX_FILES) throw new Error('This folder has too many files. Pick the skill folder itself, not its parent.');
    const path = relative.join('/');
    if (file.size > MAX_FILE_BYTES) throw new Error(`${path} is larger than 512 KB and cannot be imported.`);
    const bytes = new Uint8Array(await file.arrayBuffer());
    try {
      if (bytes.includes(0)) throw new Error('binary');
      files[path] = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    } catch { skipped.add(path); }
  }
  return {name: name || 'skill', files, skipped: [...skipped]};
}

/** What the import left out, shown inside the install review. */
export function ImportSummary({preview}: {preview: Row}) {
  const imported = preview.import;
  if (!imported) return null;
  const skipped: Row[] = imported.skipped || [];
  return <div className="skill-import-summary">
    {(imported.skills || []).map((skill: Row) => <p key={skill.id}>{skill.name} · {skill.files.length} files. Scripts and references are stored as text and are not run by TermX.</p>)}
    {skipped.length > 0 && <details><summary>{skipped.length} skipped</summary><ul>{skipped.map(item => <li key={item.path}>{item.path} · {item.reason}</li>)}</ul></details>}
  </div>;
}

export default function SkillImport({request, projectId, busy, act, onPreview}: {
  request: Request;
  projectId: string | null;
  busy: boolean;
  act: (fn: (generation: number) => Promise<unknown>, message: string) => Promise<unknown>;
  onPreview: (row: Row) => void;
}) {
  const [version, setVersion] = useState('1');
  const [sources, setSources] = useState<Row[] | null>(null);
  const review = (load: () => Promise<Row>) => act(async () => onPreview(await request<Row>('/api/workspace/skill-sources/import-preview', post({version, ...await load()}))), 'Review ready. Check the permissions below, then install.');
  return <details><summary>Import a skill folder</summary>
    <div className="manager-form">
      <p className="manager-help">Pick a folder with a SKILL.md, or a folder of skill folders. TermX reads text files only and lists anything else as skipped. Nothing is installed until you review it and choose Install reviewed version.</p>
      <label className="manager-field"><span>Version</span><Input aria-label="Imported skill version" value={version} disabled={busy} onChange={e => setVersion(e.target.value)} /></label>
      <label className="manager-field"><span>Skill folder</span>
        <input type="file" aria-label="Skill folder" disabled={busy || !version} multiple {...({webkitdirectory: '', directory: ''} as Row)}
          onChange={e => {
            const picked = e.target.files;
            if (!picked?.length) return;
            void review(() => readPickedFolder(picked));
            e.target.value = '';
          }} />
      </label>
      <div className="manager-item-actions">
        <Button variant="outline" disabled={busy} onClick={() => void act(async () => {
          const found = await request<Row>('/api/workspace/skill-sources/detected' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''));
          setSources(found.sources || []);
        }, 'Skill folders on this computer listed')}>Find skills on this computer</Button>
      </div>
      {sources && sources.length === 0 && <p role="status" className="manager-help">No skill folders found in your user or project skills or in ~/.claude/skills. Pick a folder instead.</p>}
      {(sources || []).map(row => <article className="manager-item" key={row.token}>
        <div><h3>{row.name}</h3><p className="manager-help">{row.source}{row.description ? ' · ' + row.description : ''}</p></div>
        <Button variant="outline" disabled={busy || !version} aria-label={`Review import of ${row.name}`} onClick={() => void review(async () => ({token: row.token, project_id: row.project_id ?? projectId}))}>Review import</Button>
      </article>)}
    </div>
  </details>;
}
