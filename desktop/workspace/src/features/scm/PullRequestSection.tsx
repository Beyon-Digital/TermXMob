import {useState} from 'react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Textarea} from '../../components/ui/textarea';
import {Disclosure, Field} from './Disclosure';
import type {PullRequest} from './scm-types';
import type {Scm} from './use-scm';

type Props = {scm: Scm; branch?: string};

function PullSummary({pull}: {pull: PullRequest}) {
  return (
    <article className="scm-pull" aria-label={'Pull request #' + pull.number}>
      <a href={pull.url} target="_blank" rel="noreferrer">
        #{pull.number} {pull.title}
      </a>
      <p>
        {pull.state} · {pull.mergeable} · {pull.headRefName} into {pull.baseRefName}
      </p>
      {pull.statusCheckRollup?.length > 0 && (
        <ul aria-label="Checks">
          {pull.statusCheckRollup.map((check, index) => {
            const url = check.detailsUrl || check.targetUrl;
            return (
              <li key={index}>
                {check.name || check.context}: {check.conclusion || check.state || check.status || 'pending'}
                {url && (
                  <>
                    {' '}
                    <a href={url} target="_blank" rel="noreferrer">
                      Details
                    </a>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {pull.comments?.map((item, index) => (
        <p key={'c' + index}>{item.body}</p>
      ))}
      {pull.reviews?.map((item, index) => (
        <p key={'r' + index}>
          {item.state}: {item.body}
        </p>
      ))}
    </article>
  );
}

/**
 * Pull requests, collapsed. Creating one always starts as a draft and waits for a confirmation that shows exactly what it
 * will send; inspecting, updating, commenting and review feedback work on a pull request number.
 */
export default function PullRequestSection({scm, branch}: Props) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [base, setBase] = useState('main');
  const [number, setNumber] = useState('');
  const [feedback, setFeedback] = useState('');
  const {busy} = scm;
  return (
    <Disclosure title="Pull request">
      <Field label="Title">
        <Input value={title} onChange={event => setTitle(event.target.value)} />
      </Field>
      <Field label="Description">
        <Textarea className="input scm-textarea" rows={3} value={body} onChange={event => setBody(event.target.value)} />
      </Field>
      <Field label="Base branch">
        <Input value={base} onChange={event => setBase(event.target.value)} />
      </Field>
      <p className="scm-note">
        Head {branch || 'current branch'} into {base || 'the base branch'}. New pull requests start as drafts.
      </p>
      <Button size="sm" variant="outline" disabled={busy || !title.trim() || !branch || !base.trim()} onClick={() => void scm.createPr(title, body, base, branch!)}>
        Create draft PR
      </Button>
      <Field label="Pull request number">
        <Input inputMode="numeric" value={number} onChange={event => setNumber(event.target.value)} />
      </Field>
      <div className="scm-button-row">
        <Button size="sm" variant="outline" disabled={busy || !number} onClick={() => void scm.inspectPull(number)}>
          Inspect PR and checks
        </Button>
        <Button size="sm" variant="outline" disabled={busy || !number || !title.trim()} onClick={() => void scm.updatePr(number, title, body, base)}>
          Update PR
        </Button>
      </div>
      {scm.pull && <PullSummary pull={scm.pull} />}
      <Field label="Review feedback">
        <Textarea className="input scm-textarea" rows={3} value={feedback} onChange={event => setFeedback(event.target.value)} />
      </Field>
      <div className="scm-button-row">
        <Button size="sm" variant="outline" disabled={busy || !number || !feedback.trim()} onClick={() => void scm.comment(number, feedback)}>
          Post comment
        </Button>
        <Button size="sm" variant="outline" disabled={busy || !number || !feedback.trim()} onClick={() => void scm.review(number, feedback)}>
          Post review comment
        </Button>
      </div>
    </Disclosure>
  );
}
