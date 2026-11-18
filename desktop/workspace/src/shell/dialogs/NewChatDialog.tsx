import type {FormEvent} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Select} from '../../components/ui/select';
import {HostPathField} from '../../components/HostPathPicker';
import PresetPicker, {type AgentPreset} from '../../features/PresetPicker';
import type {Project} from '../../lib/api';
import {scopeProjects} from '../../lib/project-labels';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ownerId: string;
  /** The chat was created and its composer is about to take focus: do not return focus to the opener. */
  keepFocusOnClose: () => boolean;
  projects: Project[];
  projectId: string;
  onProject: (id: string) => void;
  presets: AgentPreset[];
  presetsLoading: boolean;
  preset: string;
  onPreset: (id: string) => void;
  folder: string;
  onFolder: (path: string) => void;
  creating: boolean;
  locked: boolean;
  onCreate: () => void;
};

/** "New chat with options": a project, an agent preset or a new project folder, then Create chat. */
export default function NewChatDialog(props: Props) {
  const {presets, preset, creating, presetsLoading} = props;
  const presetMissing = !!preset && !presets.some(row => row.id === preset);
  function submit(event: FormEvent) {
    event.preventDefault();
    props.onCreate();
  }
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent
        onCloseAutoFocus={event => {
          if (props.keepFocusOnClose()) event.preventDefault();
        }}
      >
        <DialogTitle>New chat with options</DialogTitle>
        <DialogDescription>
          Choose project context or an agent preset. Your first message names the chat.
        </DialogDescription>
        <form onSubmit={submit}>
          <label>
            Project
            <Select aria-label="Project" value={props.projectId} onChange={event => props.onProject(event.target.value)}>
              <option value="">Unassigned conversation</option>
              {scopeProjects(props.projects, props.projectId).map(project => (
                <option value={project.id} key={project.id}>
                  {project.name}
                </option>
              ))}
            </Select>
          </label>
          <PresetPicker
            loading={presetsLoading}
            label="New conversation agent preset"
            presets={presets}
            value={preset}
            onChange={id => props.onPreset(id || '')}
            disabled={creating}
          />
          <HostPathField
            label="Add a project folder"
            value={props.folder}
            onChange={props.onFolder}
            ownerId={props.ownerId}
            disabled={creating || props.locked}
          />
          <Button type="submit" disabled={creating || presetsLoading || presetMissing}>
            {creating ? 'Creating…' : 'Create chat'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
