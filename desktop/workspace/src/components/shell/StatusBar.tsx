import {GitBranch, Lock, TriangleAlert} from 'lucide-react';
import type {ReactNode} from 'react';
import {Popover, PopoverContent, PopoverTrigger} from '../ui/popover';
import './chrome.css';
import './host-status.css';

export type EditorCursor = {line: number; column: number; language: string};

type Props = {
  online: boolean;
  /** The machine the workspace is connected to. Empty until the host answers. */
  hostName: string;
  branch?: string;
  /** The checkout has uncommitted changes. */
  dirty?: boolean;
  /** Language-server problems in open files. The count is hidden while it is 0. */
  problems: number;
  needYou: number;
  /** Where Main agent stands, in words ("working", "idle"); `''` hides it. */
  mainAgent: string;
  /** Position and language of the focused editor tab, or `null` when no editor is focused. */
  cursor: EditorCursor | null;
  /** Capture controls, rendered only when there is something to show. */
  capture?: ReactNode;
  /** The phone shows a health dot, the approvals badge and active capture controls (a safety indicator) only. */
  compact?: boolean;
  /** The host has not answered for more than 30 seconds: the dot turns red and the text says Disconnected. */
  disconnected?: boolean;
  /** The host health popover, when this sign-in may read it. Without it the health item is plain text. */
  hostDetails?: ReactNode;
  hostOpen?: boolean;
  onHostOpenChange?: (open: boolean) => void;
  onOpenSourceControl: () => void;
  onOpenProblems: () => void;
  onOpenAgents: () => void;
  onOpenMainAgent: () => void;
  onLock: () => void;
};

/** The 24px status bar. An item is a button only when it does something; the rest is text. */
export default function StatusBar(props: Props) {
  const {online, hostName, branch, needYou, cursor} = props;
  const health = !online ? 'Offline' : props.disconnected ? 'Disconnected' : 'Connected';
  const down = !online || !!props.disconnected;
  const label = (
    <>
      <span className={'status-health ' + (down ? 'down' : 'ok')} aria-hidden="true" />
      {health}
      {!props.compact && hostName ? ' · ' + hostName : ''}
    </>
  );
  const needs = needYou === 1 ? '1 needs you' : needYou + ' need you';
  return (
    <footer className="status-strip" aria-label="Status">
      <span className="status-item" role="status">
        {props.hostDetails ? (
          <Popover open={props.hostOpen} onOpenChange={props.onHostOpenChange}>
            <PopoverTrigger asChild>
              <button type="button" className="status-item status-button status-health-button" aria-haspopup="dialog">
                {label}
              </button>
            </PopoverTrigger>
            <PopoverContent className="host-popover" side="top" align="start">
              {props.hostDetails}
            </PopoverContent>
          </Popover>
        ) : (
          label
        )}
      </span>
      {!props.compact && branch && (
        <button type="button" className="status-item status-button" onClick={props.onOpenSourceControl}>
          <GitBranch size={12} strokeWidth={1.5} aria-hidden="true" />
          {branch}
          {props.dirty && <span aria-hidden="true">*</span>}
          {props.dirty && <span className="visually-hidden"> (uncommitted changes)</span>}
        </button>
      )}
      {!props.compact && props.problems > 0 && (
        <button type="button" className="status-item status-button" onClick={props.onOpenProblems}>
          <TriangleAlert size={12} strokeWidth={1.5} aria-hidden="true" />
          {props.problems === 1 ? '1 problem' : props.problems + ' problems'}
        </button>
      )}
      {props.capture}
      {needYou > 0 && (
        <button type="button" className="status-item status-button" onClick={props.onOpenAgents}>
          {needs}
        </button>
      )}
      <span className="status-spacer" />
      {!props.compact && props.mainAgent && (
        <button type="button" className="status-item status-button" onClick={props.onOpenMainAgent}>
          Main agent {props.mainAgent}
        </button>
      )}
      {!props.compact && cursor && (
        <span className="status-item">
          Ln {cursor.line}, Col {cursor.column}
          {cursor.language ? ' · ' + cursor.language : ''}
        </span>
      )}
      {!props.compact && (
        <button type="button" className="status-item status-button" onClick={props.onLock}>
          <Lock size={12} strokeWidth={1.5} aria-hidden="true" />
          Lock
        </button>
      )}
    </footer>
  );
}
