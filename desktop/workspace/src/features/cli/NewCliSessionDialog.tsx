import {useEffect, useState, type FormEvent} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {FormField} from '../../components/ui/form-field';
import {Select} from '../../components/ui/select';
import {Textarea} from '../../components/ui/textarea';
import type {Project} from '../../lib/api';
import type {CliAgent, CliSession, NewCliSession} from '../../lib/cli-sessions';
import {useCliSessions} from './CliSessionsProvider';
import './cli.css';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projects: readonly Project[];
  /** The project the workspace is on; the default choice. */
  projectId: string;
  onCreated: (session: CliSession) => void;
};

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/** The version number inside what the CLI printed ("2.1.326 (Claude Code)", "codex-cli 0.169.0"), or `''`. */
export const versionNumber = (version: string | null) => version?.match(/\d+(?:\.\d+)+/)?.[0] ?? '';

/** Why a CLI cannot be chosen, or `''` when it can. */
export function unavailableReason(agent: CliAgent): string {
  return agent.installed ? '' : agent.label + ' is not installed on this host.';
}

/**
 * Start a CLI session: which CLI, which project, an optional first instruction, and whether it works in a separate Git
 * worktree. Permission bypass is never offered here: it needs a restricted sandbox and a human confirmation the host asks
 * for itself.
 */
export default function NewCliSessionDialog({open, onOpenChange, projects, projectId, onCreated}: Props) {
  const cli = useCliSessions();
  const [agents, setAgents] = useState<CliAgent[] | null>(null);
  const [agentId, setAgentId] = useState('');
  const [project, setProject] = useState(projectId);
  const [prompt, setPrompt] = useState('');
  const [isolation, setIsolation] = useState<NewCliSession['isolation']>('none');
  const [mode, setMode] = useState<NewCliSession['mode']>('default');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setError('');
    setProject(current => (projects.some(item => item.id === current) ? current : projectId || projects[0]?.id || ''));
    cli
      .loadAgents()
      .then(rows => {
        if (!live) return;
        setAgents(rows);
        setAgentId(current => (rows.some(row => row.id === current && row.installed) ? current : (rows.find(row => row.installed)?.id ?? '')));
      })
      .catch(reason => live && setError(message(reason)));
    return () => {
      live = false;
    };
  }, [open]);

  const chosen = agents?.find(agent => agent.id === agentId);
  const modes = chosen?.modes ?? ['default'];
  const canWorktree = !!project;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!chosen || busy) return;
    setBusy(true);
    setError('');
    try {
      const body: NewCliSession = {
        cli: chosen.id,
        isolation: canWorktree ? isolation : 'none',
        mode: modes.includes(mode) ? mode : 'default',
        ...(project ? {project_id: project} : {}),
        ...(prompt.trim() ? {prompt: prompt.trim()} : {}),
      };
      const session = await cli.create(body);
      onOpenChange(false);
      setPrompt('');
      onCreated(session);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cli-dialog" aria-describedby="cli-dialog-description">
        <DialogTitle>New CLI session</DialogTitle>
        <DialogDescription id="cli-dialog-description">Run a coding CLI in its own terminal. It keeps running if you close its tab.</DialogDescription>
        <form className="cli-form" onSubmit={event => void submit(event)}>
          <FormField label="CLI" error={agents && !agents.some(agent => agent.installed) ? 'No supported CLI is installed on this host.' : undefined}>
            {control => (
              <Select {...control} value={agentId} disabled={!agents} onChange={event => setAgentId(event.target.value)}>
                {!agents && <option value="">Loading…</option>}
                {agents?.map(agent => (
                  <option key={agent.id} value={agent.id} disabled={!agent.installed}>
                    {agent.label}
                    {agent.installed ? (versionNumber(agent.version) ? ' ' + versionNumber(agent.version) : '') : ' (not installed)'}
                  </option>
                ))}
              </Select>
            )}
          </FormField>
          {agents && agents.some(agent => !agent.installed) && (
            <ul className="cli-reasons">
              {agents
                .filter(agent => !agent.installed)
                .map(agent => (
                  <li key={agent.id}>{unavailableReason(agent)}</li>
                ))}
            </ul>
          )}
          <FormField label="Project">
            {control => (
              <Select {...control} value={project} onChange={event => setProject(event.target.value)}>
                <option value="">Default terminal folder</option>
                {projects.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </Select>
            )}
          </FormField>
          <FormField label="First instruction" hint="Optional. It is typed into the CLI after it starts.">
            {control => <Textarea {...control} className="input cli-prompt" rows={3} value={prompt} onChange={event => setPrompt(event.target.value)} />}
          </FormField>
          <FormField
            label="Where it works"
            hint={isolation === 'worktree' ? 'A new branch checkout under this project. Your own checkout is not touched.' : 'The project folder itself.'}
          >
            {control => (
              <Select {...control} value={canWorktree ? isolation : 'none'} disabled={!canWorktree} onChange={event => setIsolation(event.target.value as NewCliSession['isolation'])}>
                <option value="none">Project folder</option>
                <option value="worktree">Separate worktree</option>
              </Select>
            )}
          </FormField>
          <FormField label="Permissions" hint={mode === 'read_only' ? 'The CLI starts in its read-only mode.' : 'The CLI asks before it edits files or runs commands, as it normally does.'}>
            {control => (
              <Select {...control} value={modes.includes(mode) ? mode : 'default'} onChange={event => setMode(event.target.value as NewCliSession['mode'])}>
                <option value="default">Default</option>
                {modes.includes('read_only') && <option value="read_only">Read only</option>}
              </Select>
            )}
          </FormField>
          {error && (
            <p className="cli-error" role="alert">
              {error}
            </p>
          )}
          <div className="cli-form-actions">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !chosen}>
              {busy ? 'Starting…' : 'Start session'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
