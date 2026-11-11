import {useState} from 'react';
import {Bookmark, Folder, Layers3, Plus} from 'lucide-react';
import {MusicMark} from './icons/WorkspaceMarks';
import {Button} from './ui/button';
import StudioProjectsDialog from './StudioProjectsDialog';
import type {Project} from '../lib/api';
import type {StudioProject, StudioProjectsController} from '../lib/studio-projects';
import type {StudioSection} from '../lib/workspace-mode';

export default function StudioNavigation({section, onSection, projects, projectId, onProject, loading = false, managedProjects, projectChangeBlocked, onBeforeArchive}: {
  section: StudioSection; onSection: (section: StudioSection) => void; projects: Project[];
  projectId: string | null; onProject: (id: string) => void; loading?: boolean;
  managedProjects?: StudioProjectsController; projectChangeBlocked?: string; onBeforeArchive?: (project: StudioProject) => Promise<boolean>;
}) {
  const [projectsOpen, setProjectsOpen] = useState(false), [initialCreate, setInitialCreate] = useState(false);
  function openProjects(create = false) {setInitialCreate(create); setProjectsOpen(true);}
  const managed = managedProjects?.projects.filter(project => !project.archived) || [];
  return <div className="studio-navigation-content">
    <nav className="studio-primary-navigation" aria-label="Studio views">
      <button type="button" className="studio-nav-item" aria-current={section === 'create' ? 'page' : undefined} aria-controls="studio-workspace" onClick={() => onSection('create')}><Plus size={16} aria-hidden="true"/>Create</button>
      <button type="button" className="studio-nav-item" aria-current={section === 'music' ? 'page' : undefined} aria-controls="studio-workspace" onClick={() => onSection('music')}><MusicMark size={16}/>Music</button>
      <button type="button" className="studio-nav-item" aria-current={section === 'library' ? 'page' : undefined} aria-controls="studio-workspace" onClick={() => onSection('library')}><Layers3 size={16} aria-hidden="true"/>Library</button>
      <button type="button" className="studio-nav-item" aria-current={section === 'assets' ? 'page' : undefined} aria-controls="studio-workspace" onClick={() => onSection('assets')}><Bookmark size={16} aria-hidden="true"/>Saved assets</button>
    </nav>
    <div className="studio-project-heading-actions"><div className="sidebar-section-heading studio-project-heading">Projects</div>{managedProjects && <Button variant="ghost" size="icon" aria-label="Create project" title="Create project" disabled={managedProjects.busy} onClick={() => openProjects(true)}><Plus size={16} aria-hidden="true"/></Button>}</div>
    <nav className="studio-project-navigation" aria-label="Studio projects" aria-busy={loading || managedProjects?.loading}>
      <button type="button" className="studio-nav-item" aria-pressed={!projectId} disabled={managedProjects?.busy} onClick={() => onProject('')}><span>No project</span></button>
      {managed.map(project => <button type="button" key={project.id} className="studio-nav-item studio-managed-project" aria-pressed={projectId === project.id} title={project.description || project.name} disabled={managedProjects?.busy} onClick={() => onProject(project.id)}><span>{project.name}</span></button>)}
      {managedProjects?.loading && <p className="studio-navigation-status" role="status">Loading Studio projects…</p>}
      {managedProjects?.error && <div className="studio-project-scope-note"><p role="alert">{managedProjects.error}</p><Button variant="outline" disabled={managedProjects.busy} onClick={managedProjects.reload}>Retry projects</Button></div>}
      {managedProjects && <Button variant="ghost" className="studio-project-manage" disabled={managedProjects.busy} onClick={() => openProjects()}>Manage projects</Button>}
      {!!projects.length && <p className="studio-project-group-label">Agent projects</p>}
      {projects.map(project => <button type="button" key={project.id} className="studio-nav-item" aria-pressed={projectId === project.id} title={project.path} disabled={loading || managedProjects?.busy} onClick={() => onProject(project.id)}><Folder size={16} aria-hidden="true"/><span>{project.name}</span></button>)}
      {loading && <p className="studio-navigation-status" role="status">Loading Agent projects…</p>}
    </nav>
    {managedProjects && <StudioProjectsDialog open={projectsOpen} onOpenChange={setProjectsOpen} initialCreate={initialCreate} controller={managedProjects} projectId={projectId} onProject={onProject} projectChangeBlocked={projectChangeBlocked} onBeforeArchive={onBeforeArchive}/>}
  </div>;
}
