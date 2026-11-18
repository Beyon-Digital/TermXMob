import {Fragment} from 'react';
import {splitReviewDiff, type ReviewDiffLine} from '../../lib/change-diff';
import {EXPAND_STEP, type Block, type GapBlock} from './diff-blocks';
import {newRange, type Hunk} from './patch';

export type DiffMode = 'inline' | 'split';

type HunkAction = {label: string; busy: boolean; run: (hunk: Hunk) => void};

type Props = {
  blocks: Block[];
  mode: DiffMode;
  total: number;
  action: HunkAction | null;
  onExpand: (gap: number, hidden: number) => void;
};

const SIGN = {addition: '+', deletion: '−', context: '', hunk: '', meta: ''} as const;
const SAID = {addition: 'Added ', deletion: 'Removed '} as const;

function InlineRow({line}: {line: ReviewDiffLine}) {
  if (line.kind === 'meta') {
    return (
      <div className="diff-row diff-meta">
        <code>{line.text}</code>
      </div>
    );
  }
  return (
    <div className={'diff-row diff-' + line.kind}>
      <span className="diff-num" aria-hidden="true">
        {line.oldLine}
      </span>
      <span className="diff-num" aria-hidden="true">
        {line.newLine}
      </span>
      <span className="diff-sign" aria-hidden="true">
        {SIGN[line.kind]}
      </span>
      <code>
        {(line.kind === 'addition' || line.kind === 'deletion') && <span className="visually-hidden">{SAID[line.kind]}</span>}
        {line.text || ' '}
      </code>
    </div>
  );
}

function Cell({line, side}: {line: ReviewDiffLine | null; side: 'before' | 'after'}) {
  return (
    <div className={'diff-cell ' + (line ? 'diff-' + line.kind : 'diff-empty')}>
      <span className="diff-num" aria-hidden="true">
        {side === 'before' ? line?.oldLine : line?.newLine}
      </span>
      <span className="diff-sign" aria-hidden="true">
        {line ? SIGN[line.kind] : ''}
      </span>
      <code>
        {line && (line.kind === 'addition' || line.kind === 'deletion') && <span className="visually-hidden">{SAID[line.kind]}</span>}
        {line?.text || ' '}
      </code>
    </div>
  );
}

function SplitRows({lines}: {lines: ReviewDiffLine[]}) {
  return (
    <>
      {splitReviewDiff(lines).map((row, index) =>
        row.kind === 'line' ? (
          <div className="diff-split-row" key={index}>
            <Cell line={row.before} side="before" />
            <Cell line={row.after} side="after" />
          </div>
        ) : (
          <div className="diff-row diff-meta" key={index}>
            <code>{row.text}</code>
          </div>
        ),
      )}
    </>
  );
}

function Gap({block, mode, onExpand}: {block: GapBlock; mode: DiffMode; onExpand: Props['onExpand']}) {
  const step = Math.min(EXPAND_STEP, block.hidden);
  const button =
    block.hidden > 0 ? (
      <button type="button" className="diff-expand" onClick={() => onExpand(block.gap, block.hidden)}>
        {block.hidden <= EXPAND_STEP ? 'Show ' + block.hidden + ' unchanged ' + (block.hidden === 1 ? 'line' : 'lines') : 'Show ' + step + ' more of ' + block.hidden + ' unchanged lines'}
      </button>
    ) : null;
  const rows = mode === 'split' ? <SplitRows lines={block.rows} /> : block.rows.map((line, index) => <InlineRow key={index} line={line} />);
  return block.edge === 'bottom' ? (
    <>
      {rows}
      {button}
    </>
  ) : (
    <>
      {button}
      {rows}
    </>
  );
}

/**
 * The lines of a diff, inline or side by side. Every hunk is a labelled group whose header can be focused (F7 and Shift+F7 move
 * between them) and carries the hunk's stage or unstage button. An added or removed line says so in words for a screen reader;
 * the sign in the margin and the tint repeat it for the eye.
 */
export default function DiffBody({blocks, mode, total, action, onExpand}: Props) {
  return (
    <>
      {blocks.map(block => {
        if (block.type === 'gap') return <Gap key={'gap-' + block.gap} block={block} mode={mode} onExpand={onExpand} />;
        const {hunk} = block;
        const range = newRange(hunk);
        return (
          <Fragment key={hunk.index}>
            <div
              className="diff-hunk"
              role="group"
              data-hunk={hunk.index}
              tabIndex={-1}
              aria-label={'Change ' + (hunk.index + 1) + ' of ' + total + ', lines ' + range.from + ' to ' + range.to}
            >
              <span className="diff-hunk-range">
                {range.from === range.to ? 'Line ' + range.from : 'Lines ' + range.from + ' to ' + range.to}
              </span>
              {hunk.heading && <span className="diff-hunk-heading">{hunk.heading}</span>}
              {action && (
                <button type="button" className="diff-hunk-action" disabled={action.busy} onClick={() => action.run(hunk)}>
                  {action.label}
                </button>
              )}
            </div>
            {mode === 'split' ? <SplitRows lines={hunk.lines} /> : hunk.lines.map((line, index) => <InlineRow key={index} line={line} />)}
          </Fragment>
        );
      })}
    </>
  );
}
