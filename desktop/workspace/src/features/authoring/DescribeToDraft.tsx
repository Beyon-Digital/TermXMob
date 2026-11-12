import {useEffect, useId, useRef, useState, type FormEvent} from 'react';
import {TriangleAlert} from 'lucide-react';
import {PromptInputBody, PromptInputSubmit, PromptInputTextarea} from '../../components/ai-elements/prompt-input';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {MAX_MESSAGES, MAX_MESSAGE_CHARS, draftDefinition, type DraftKind, type DraftMessage} from './draft-api';
import {kindCopy} from './kinds';
import './authoring.css';

export interface DescribeToDraftProps {
  kind: DraftKind;
  open: boolean;
  onClose: () => void;
  /** The person accepted the draft. The caller opens its own editor pre-filled with it. */
  onReview: (draft: Record<string, unknown>) => void;
  /** The person chose to type the details instead. */
  onManual: () => void;
  /** Offered when no model account is set up. Leave it out where the caller cannot navigate to settings. */
  onSetupProvider?: () => void;
  /** Where a skill would be saved, so the host can check the same permission as saving. */
  projectId?: string | null;
}

export function DescribeToDraft(props: DescribeToDraftProps) {
  const {open, onClose} = props;
  const copy = kindCopy[props.kind];
  const descriptionId = useId();
  return (
    <Dialog open={open} onOpenChange={next => { if (!next) onClose(); }}>
      <DialogContent
        className="describe-dialog"
        aria-describedby={descriptionId}
        onOpenAutoFocus={event => {
          event.preventDefault();
          (event.currentTarget as HTMLElement).querySelector<HTMLTextAreaElement>('textarea')?.focus();
        }}
      >
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription id={descriptionId}>
          Describe it in a sentence or two. Your words go to your model account, and nothing is saved until you review and create it.
        </DialogDescription>
        <Surface {...props} />
      </DialogContent>
    </Dialog>
  );
}

function Surface({kind, onClose, onReview, onManual, onSetupProvider, projectId}: DescribeToDraftProps) {
  const copy = kindCopy[kind];
  const [messages, setMessages] = useState<DraftMessage[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [needsProvider, setNeedsProvider] = useState(false);
  const inflight = useRef<AbortController | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => () => inflight.current?.abort(), []);

  const atLimit = messages.length >= MAX_MESSAGES;

  async function send(history: DraftMessage[]) {
    if (inflight.current) return;
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setError('');
    setNeedsProvider(false);
    try {
      const result = await draftDefinition({kind, messages: history, currentDraft: draft, projectId}, controller.signal);
      if (controller.signal.aborted) return;
      if (result.status === 'no_provider') {
        setNeedsProvider(true);
        return;
      }
      setMessages(result.reply ? [...history, {role: 'assistant', text: result.reply}] : history);
      if (result.status === 'drafted' && result.draft) {
        setDraft(result.draft);
        setWarnings(result.warnings);
        setModel(result.model ?? '');
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'The draft could not be made. Try again.');
    } finally {
      if (inflight.current === controller) {
        inflight.current = null;
        setBusy(false);
        box.current?.focus();
      }
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy || atLimit) return;
    const history: DraftMessage[] = [...messages, {role: 'user', text}];
    setMessages(history);
    setInput('');
    void send(history);
  }

  function startOver() {
    inflight.current?.abort();
    inflight.current = null;
    setMessages([]);
    setDraft(null);
    setWarnings([]);
    setError('');
    setNeedsProvider(false);
    setBusy(false);
    box.current?.focus();
  }

  return (
    <div className="describe-body">
      <div className="describe-thread-head">
        <h3>Conversation</h3>
        {messages.length > 0 && (
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={startOver}>Start over</Button>
        )}
      </div>
      <div className="describe-thread" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions">
        {messages.length === 0 && <p className="describe-empty">Say what it should do. For example: {copy.example}</p>}
        {messages.map((message, index) =>
          message.role === 'user'
            ? <p key={index} className="describe-user"><span className="sr-only">You: </span>{message.text}</p>
            : <p key={index} className="describe-assistant"><span className="sr-only">Draft assistant: </span>{message.text}</p>)}
      </div>
      {busy && <p role="status" className="describe-pending">Drafting…</p>}
      {error && (
        <div role="alert" className="describe-error">
          <span>{error}</span>
          <Button type="button" variant="ghost" size="sm" onClick={() => void send(messages)}>Try again</Button>
        </div>
      )}
      {needsProvider && (
        <div role="alert" className="describe-error">
          <span>No model account is set up, so a draft cannot be written yet.{!onSetupProvider && ' Add one in Settings, under Models and engines.'}</span>
          {onSetupProvider && <Button type="button" variant="outline" size="sm" onClick={() => { onSetupProvider(); onClose(); }}>Set up a model provider</Button>}
        </div>
      )}
      {draft && (
        <section className="describe-draft" aria-label="Draft">
          <div className="describe-draft-head">
            <h3>Draft</h3>
            {model && <small>Written by {model}</small>}
          </div>
          <dl className="describe-rows">
            {copy.rows(draft).map(row => (
              <div key={row.label} className="describe-row">
                <dt>{row.label}</dt>
                <dd>
                  {row.long
                    ? <pre className="describe-long" role="region" aria-label={row.label} tabIndex={0}>{row.value || 'Not set'}</pre>
                    : row.value || <span className="describe-unset">Not set</span>}
                </dd>
              </div>
            ))}
          </dl>
          {warnings.length > 0 && (
            <ul className="describe-warnings" aria-label="Notes about this draft">
              {warnings.map(warning => (
                <li key={warning}><TriangleAlert size={16} strokeWidth={1.5} aria-hidden="true" />{warning}</li>
              ))}
            </ul>
          )}
        </section>
      )}
      <form className="describe-composer" onSubmit={submit} aria-busy={busy}>
        <PromptInputBody>
          <PromptInputTextarea
            ref={box}
            aria-label={draft ? `Ask for a change to the ${copy.noun}` : copy.composerLabel}
            placeholder={draft ? 'Ask for a change…' : `Describe the ${copy.noun}…`}
            rows={2}
            maxLength={MAX_MESSAGE_CHARS}
            value={input}
            disabled={atLimit}
            onChange={event => setInput(event.target.value)}
          />
        </PromptInputBody>
        <PromptInputSubmit
          aria-label={draft ? 'Send change' : 'Send description'}
          variant={draft ? 'outline' : 'default'}
          status={busy ? 'submitted' : undefined}
          disabled={busy || atLimit || !input.trim()}
        />
      </form>
      {atLimit && <p className="describe-limit">This conversation is long. Review the draft, or start over with a shorter description.</p>}
      <div className="describe-actions">
        <Button type="button" variant="outline" onClick={onManual}>Fill in manually</Button>
        {draft && <Button type="button" disabled={busy} onClick={() => onReview(draft)}>Review and create</Button>}
      </div>
    </div>
  );
}
