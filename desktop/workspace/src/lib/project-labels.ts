/**
 * Folders the host creates for itself: a bot's scratch folder (scratch/<owner hash>/<id>) and a parallel
 * worktree (worktrees/<id>). They are enrolled as projects so tools can run in them, but nobody picks
 * them as a place to put settings, and their names are hashes.
 */
const MANAGED = /[\\/](?:scratch[\\/][0-9a-f]{32,64}[\\/][0-9a-f]{16,64}|worktrees[\\/][0-9a-f]{12,64})(?:[\\/]|$)/i;

export const isManagedFolder = (project: {path?: string}) => MANAGED.test(project.path ?? '');

/** Projects a person can choose as a settings scope; the one already in use stays listed so the control never loses its value. */
export const scopeProjects = <T extends {id: string; path?: string}>(projects: T[], current: string) =>
  projects.filter(project => project.id === current || !isManagedFolder(project));
