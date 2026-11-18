import {gql, request, type Project, type Session} from '../../lib/api';

export type Pty = {id: string; title: string; cwd: string; exited: boolean};

const SESSIONS = 'query{sessions{id title cwd exited}}';

/** The running shells this checkout may attach to: live ones whose folder is the conversation's (or the project's). */
export async function listPtys(project: Project | null, executionRoot: string | undefined): Promise<Pty[]> {
  const data = await gql<{sessions: Pty[]}>(SESSIONS);
  return data.sessions.filter(row => !row.exited && (!project || row.cwd === (executionRoot || project.path)));
}

/** Start a shell in the conversation's checkout, or in the project folder when there is no conversation. */
export async function createPty(project: Project, workspaceSession: string | undefined): Promise<Pty> {
  if (workspaceSession) return request<Pty>('/api/workspace/sessions/' + workspaceSession + '/terminal', {method: 'POST'});
  const data = await gql<{create_session: Pty}>(
    'mutation($input:CreateSessionInput!){create_session(input:$input){id title cwd exited}}',
    {input: {cwd: project.path, sandbox_profile: 'workspace', title: project.name}},
  );
  return data.create_session;
}

/** End a shell. */
export async function deletePty(id: string): Promise<void> {
  await gql<{delete_session: {ok: boolean}}>('mutation($id:String!){delete_session(session_id:$id){ok}}', {id});
}

export type PtyTarget = {project: Project | null; session: Session | null};
