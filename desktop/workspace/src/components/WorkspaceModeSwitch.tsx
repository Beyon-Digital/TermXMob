import {useRef, type KeyboardEvent} from 'react';
import {AgentMark,StudioMark,DataMark} from './icons/WorkspaceMarks';
import type {WorkspaceMode} from '../lib/workspace-mode';

/** A shared workspace choice; arrow keys select and focus the adjacent destination. */
export default function WorkspaceModeSwitch({value, onChange, presentation='inline'}: {value: WorkspaceMode; onChange: (mode: WorkspaceMode) => void; presentation?: 'inline'|'rail'}) {
  const root = useRef<HTMLDivElement>(null);
  function navigate(event: KeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', ...(presentation === 'rail' ? ['ArrowUp', 'ArrowDown'] : [])].includes(event.key)) return;
    event.preventDefault();
    const modes: WorkspaceMode[] = ['agent','studio','data'];
    const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
    const next = event.key === 'Home' ? modes[0] : event.key === 'End' ? modes.at(-1)! : modes[(modes.indexOf(value)+step+modes.length)%modes.length];
    if (next !== value) onChange(next);
    root.current?.querySelector<HTMLButtonElement>('[data-workspace-choice="' + next + '"]')?.focus();
  }
  return <div className={'workspace-mode-switch'+(presentation==='rail'?' workspace-mode-rail':'')} role="group" aria-label="Workspace" ref={root}>
    <button type="button" data-workspace-choice="agent" title={presentation==='rail'?'Agent':undefined} aria-pressed={value === 'agent'} aria-controls="agent-workspace" tabIndex={value === 'agent' ? 0 : -1} onKeyDown={navigate} onClick={() => {if (value !== 'agent') onChange('agent')}}><AgentMark size={20} aria-hidden="true"/><span className={presentation==='rail'?'visually-hidden':undefined}>Agent</span></button>
    <button type="button" data-workspace-choice="studio" title={presentation==='rail'?'Studio':undefined} aria-pressed={value === 'studio'} aria-controls="studio-workspace" tabIndex={value === 'studio' ? 0 : -1} onKeyDown={navigate} onClick={() => {if (value !== 'studio') onChange('studio')}}><StudioMark size={20} aria-hidden="true"/><span className={presentation==='rail'?'visually-hidden':undefined}>Studio</span></button>
    <button type="button" data-workspace-choice="data" title={presentation==='rail'?'Data':undefined} aria-pressed={value === 'data'} aria-controls="data-workspace" tabIndex={value === 'data' ? 0 : -1} onKeyDown={navigate} onClick={() => {if (value !== 'data') onChange('data')}}><DataMark size={20} aria-hidden="true"/><span className={presentation==='rail'?'visually-hidden':undefined}>Data</span></button>
  </div>;
}
