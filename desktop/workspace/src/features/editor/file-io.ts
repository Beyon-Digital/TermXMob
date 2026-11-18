import {gql, json, request, type Project, type Session} from '../../lib/api';

/**
 * Reading, listing, searching and saving files on the host: the calls the editor, the Explorer, Search and Quick open make.
 *
 * A conversation with a local checkout reads through `/api/workspace/sessions/:id/files` (so it sees that checkout's
 * worktree); a conversation without one, or a cloud runner, reads through the project's GraphQL fields. The requests are the
 * ones `Workbench.tsx` makes; this module is the one place the new layout makes them.
 */

/** What a file-reading call is aimed at: a project and, usually, a conversation's checkout of it. */
export type FileTarget = {project: Project | null; session: Session | null};

export type FileEntry = {name: string; path: string; directory: boolean; dir?: boolean; is_dir?: boolean; kind?: string};
export type SearchHit = {path: string; line?: number; text?: string};
export type FileContent = {path: string; content: string; revision: string; editable: boolean; reason?: string};

export const isDirectory = (entry: FileEntry) => !!(entry.directory || entry.dir || entry.is_dir || entry.kind === 'directory');

/** The folder the conversation works in, falling back to the project's own. */
export const executionRoot = (target: FileTarget) => target.session?.cwd || target.project?.path || '';

/** The key of an open buffer: one per project, checkout folder and path. */
export const bufferKey = (target: FileTarget, path: string) => (target.project?.id ?? '') + ':' + executionRoot(target) + ':' + path;

/** Whether the host's session file API serves this conversation (a local checkout, not a cloud runner). */
const viaSession = (target: FileTarget) => !!target.session && !target.session.runner_id;

export const filesPath = (id: string, worktree: string | null | undefined, operation: string, path = '', query = '') =>
  '/api/workspace/sessions/' +
  id +
  '/files?' +
  new URLSearchParams({operation, path, query, ...(worktree ? {worktree_id: worktree} : {})});

export async function listTree(target: FileTarget, path = ''): Promise<FileEntry[]> {
  const {project, session} = target;
  if (!project) return [];
  if (viaSession(target)) {
    return (await request<{entries: FileEntry[]}>(filesPath(session!.id, session!.worktree_id, 'tree', path))).entries ?? [];
  }
  const data = await gql<{project_tree: {entries: FileEntry[]}}>(
    'query($id:String!,$path:String!){project_tree(project_id:$id,path:$path){entries}}',
    {id: project.id, path},
  );
  return data.project_tree.entries ?? [];
}

export async function readFile(target: FileTarget, path: string): Promise<FileContent> {
  const {project, session} = target;
  if (!project) throw new Error('Open a project to read files.');
  if (viaSession(target)) return request<FileContent>(filesPath(session!.id, session!.worktree_id, 'read', path));
  const data = await gql<{project_file: FileContent}>(
    'query($id:String!,$path:String!){project_file(project_id:$id,path:$path){path content revision editable reason}}',
    {id: project.id, path},
  );
  return data.project_file;
}

/** The buffer fields a save needs: where the content came from and the revision it was read at. */
export type SaveInput = {
  project_id: string;
  path: string;
  content: string;
  revision: string;
  workspaceSession?: string;
  worktree?: string | null;
};

export async function writeFile(input: SaveInput): Promise<string> {
  if (input.workspaceSession) {
    const saved = await request<{revision: string}>(
      '/api/workspace/sessions/' + input.workspaceSession + '/files',
      json('PUT', {path: input.path, content: input.content, revision: input.revision, worktree_id: input.worktree}),
    );
    return saved.revision;
  }
  const data = await gql<{save_project_file: {revision: string}}>(
    'mutation($id:String!,$input:FileSaveInput!){save_project_file(project_id:$id,input:$input)}',
    {id: input.project_id, input: {path: input.path, content: input.content, revision: input.revision}},
  );
  return data.save_project_file.revision;
}

/** File names matching `query` in the conversation's checkout. Local checkouts only. */
export async function quickOpen(target: FileTarget, query: string): Promise<{results: {path: string}[]; truncated: boolean}> {
  const {session} = target;
  if (!session || session.runner_id) throw new Error('Quick open targets local checkouts. Cloud source uploads are managed in Runners.');
  const found = await request<{results: {path: string}[]; truncated: boolean}>(
    filesPath(session.id, session.worktree_id, 'quick-open', '', query),
  );
  return {results: found.results ?? [], truncated: !!found.truncated};
}

/** Text search across the checkout. */
export async function searchFiles(target: FileTarget, query: string): Promise<SearchHit[]> {
  const {project, session} = target;
  if (!project || !query) return [];
  if (viaSession(target)) {
    const found = await request<{results: SearchHit[]}>(filesPath(session!.id, session!.worktree_id, 'search', '', query));
    return found.results ?? [];
  }
  const data = await gql<{project_search: {results: SearchHit[]}}>(
    'query($id:String!,$input:FileSearchInput!){project_search(project_id:$id,input:$input){results{path line text}}}',
    {id: project.id, input: {query}},
  );
  return data.project_search.results ?? [];
}
