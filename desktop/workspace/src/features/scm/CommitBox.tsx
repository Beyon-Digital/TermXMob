import {useId, type KeyboardEvent, type RefObject} from 'react';
import {Button} from '../../components/ui/button';
import {Textarea} from '../../components/ui/textarea';

export const SUBJECT_LIMIT = 72;

export type Primary = {
  kind: 'commit' | 'push';
  label: string;
  /** Why the button is off, in one sentence. Empty when it is on. */
  reason: string;
  /** A line under an enabled button (how many commits Push will send). */
  note?: string;
};

type Props = {
  message: string;
  onMessage: (message: string) => void;
  primary: Primary;
  busy: boolean;
  textarea: RefObject<HTMLTextAreaElement | null>;
  onCommit: () => void;
  onPrimary: () => void;
};

/** The commit message and the view's one primary button. Mod+Enter commits from the box. */
export default function CommitBox({message, onMessage, primary, busy, textarea, onCommit, onPrimary}: Props) {
  const id = useId();
  const subject = message.split('\n')[0].length;
  const hint = subject > SUBJECT_LIMIT ? 'Subject is ' + subject + ' characters. Aim for ' + SUBJECT_LIMIT + ' or fewer.' : 'Subject ' + subject + ' of ' + SUBJECT_LIMIT;
  const key = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey)) return;
    event.preventDefault();
    onCommit();
  };
  return (
    <div className="view-section view-section-first scm-commit">
      <label className="scm-field" htmlFor={id + '-message'}>
        <span>Commit message</span>
      </label>
      <Textarea
        id={id + '-message'}
        ref={textarea}
        className="input scm-textarea"
        rows={3}
        value={message}
        disabled={busy}
        aria-describedby={id + '-hint'}
        onChange={event => onMessage(event.target.value)}
        onKeyDown={key}
      />
      <p className="scm-hint" id={id + '-hint'} data-over={subject > SUBJECT_LIMIT || undefined}>
        {hint}
      </p>
      <Button className="scm-primary" disabled={busy || !!primary.reason} aria-describedby={primary.reason || primary.note ? id + '-reason' : undefined} onClick={onPrimary}>
        {primary.label}
      </Button>
      {(primary.reason || primary.note) && (
        <p className="scm-hint" id={id + '-reason'}>
          {primary.reason || primary.note}
        </p>
      )}
    </div>
  );
}
