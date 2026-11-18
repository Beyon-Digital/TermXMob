import {ChevronDown, ChevronUp, FileText, RefreshCw} from 'lucide-react';
import type {DiffMode} from './DiffBody';
import type {Side} from './scm-types';

type Props = {
  path: string;
  side: Side;
  counts: {additions: number; deletions: number} | null;
  mode: DiffMode;
  /** The tab is too narrow for two columns. */
  narrow: boolean;
  canStep: boolean;
  busy: boolean;
  /** Stage or unstage the whole file. */
  fileAction: {label: string; run: () => void} | null;
  onOpenFile: (() => void) | null;
  onMode: (mode: DiffMode) => void;
  onStep: (step: 'next' | 'previous') => void;
  onRefresh: () => void;
};

/** The diff tab's 36px bar: the file, how much changed, change navigation, the inline or side by side switch, and file actions. */
export default function DiffToolbar(props: Props) {
  const {mode, narrow} = props;
  return (
    <div className="diff-bar">
      <div className="diff-bar-title">
        <span className="diff-bar-path" title={props.path}>
          {props.path}
        </span>
        <span className="diff-bar-side">{props.side === 'staged' ? 'Staged' : 'Changes'}</span>
        {props.counts && (
          <span className="diff-bar-counts">
            <span>+{props.counts.additions}</span>
            <span>−{props.counts.deletions}</span>
          </span>
        )}
      </div>
      <div className="diff-bar-actions">
        <button type="button" className="scm-icon" aria-label="Previous change" title="Previous change (Shift+F7)" disabled={!props.canStep} onClick={() => props.onStep('previous')}>
          <ChevronUp size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
        <button type="button" className="scm-icon" aria-label="Next change" title="Next change (F7)" disabled={!props.canStep} onClick={() => props.onStep('next')}>
          <ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
        <div className="diff-modes" role="group" aria-label="Diff view">
          <button type="button" aria-pressed={mode === 'inline' || narrow} onClick={() => props.onMode('inline')}>
            Inline
          </button>
          <button
            type="button"
            aria-pressed={mode === 'split' && !narrow}
            disabled={narrow}
            title={narrow ? 'Side by side needs a wider tab' : undefined}
            onClick={() => props.onMode('split')}
          >
            Side by side
          </button>
        </div>
        {props.fileAction && (
          <button type="button" className="scm-text-action" disabled={props.busy} onClick={props.fileAction.run}>
            {props.fileAction.label}
          </button>
        )}
        {props.onOpenFile && (
          <button type="button" className="scm-icon" aria-label="Open file in editor" title="Open file in editor" onClick={props.onOpenFile}>
            <FileText size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
        <button type="button" className="scm-icon" aria-label="Refresh diff" title="Refresh diff" onClick={props.onRefresh}>
          <RefreshCw size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
