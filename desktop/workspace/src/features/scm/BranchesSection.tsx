import {useState} from 'react';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {Disclosure, Field} from './Disclosure';
import type {DeliveryMeta} from './scm-types';
import type {Scm} from './use-scm';

type Props = {
  scm: Scm;
  meta: DeliveryMeta | null;
  /** The worktree the conversation works in, or `''` for the project's own checkout. */
  currentId: string;
  remote: string;
  onRemote: (remote: string) => void;
};

/** Branches, the remote that Push uses, and the project's parallel worktrees (use one, remove one). */
export default function BranchesSection({scm, meta, currentId, remote, onRemote}: Props) {
  const [branch, setBranch] = useState('');
  const [base, setBase] = useState('main');
  const {busy} = scm;
  const remotes = meta?.remotes.length ? meta.remotes : ['origin'];
  const trees = meta?.worktrees ?? [];
  return (
    <Disclosure title="Branches and worktrees">
      <Field label="Remote for push">
        <Select value={remote} onChange={event => onRemote(event.target.value)}>
          {remotes.map(name => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Branch name">
        <Input value={branch} onChange={event => setBranch(event.target.value)} placeholder="feature/my-change" />
      </Field>
      <Field label="Base ref">
        <Input value={base} onChange={event => setBase(event.target.value)} />
      </Field>
      <div className="scm-button-row">
        <Button size="sm" variant="outline" disabled={busy || !branch.trim()} onClick={() => void scm.switchBranch(branch.trim(), true)}>
          Create branch
        </Button>
        <Button size="sm" variant="outline" disabled={busy || !branch.trim()} onClick={() => void scm.switchBranch(branch.trim(), false)}>
          Switch branch
        </Button>
        <Button size="sm" variant="outline" disabled={busy || !branch.trim()} onClick={() => void scm.createWorktree(branch.trim(), base.trim() || 'HEAD')}>
          New worktree
        </Button>
      </div>
      {trees.length > 0 && (
        <ul className="scm-worktrees" aria-label="Worktrees">
          {trees.map(tree => (
            <li key={tree.id}>
              <span className="scm-worktree-name" title={tree.path}>
                {tree.branch}
              </span>
              {tree.id === currentId ? (
                <span className="scm-note">Current</span>
              ) : (
                <span className="scm-button-row">
                  <Button size="sm" variant="ghost" disabled={busy} aria-label={'Use worktree ' + tree.branch} onClick={() => void scm.chooseCheckout(tree.id)}>
                    Use
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} aria-label={'Remove worktree ' + tree.branch} onClick={() => void scm.removeWorktree(tree.id)}>
                    Remove
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Disclosure>
  );
}
