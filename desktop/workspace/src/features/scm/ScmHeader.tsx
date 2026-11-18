import {ArrowDown, ArrowUp, Ellipsis, GitBranch, RefreshCw} from 'lucide-react';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger} from '../../components/ui/dropdown-menu';
import {Select} from '../../components/ui/select';
import type {DeliveryMeta} from './scm-types';

type Props = {
  branch?: string | null;
  meta: DeliveryMeta | null;
  /** The worktree the conversation works in, or `''` for the project's own checkout. */
  currentId: string;
  busy: boolean;
  disableCheckout: boolean;
  onCheckout: (id: string) => void;
  onRefresh: () => void;
  onFetch: () => void;
  onPull: () => void;
  onPush: () => void;
};

/** The branch with how far it is ahead of or behind its upstream, the checkout picker, and Fetch, Pull and Push in a menu. */
export default function ScmHeader({branch, meta, currentId, busy, disableCheckout, onCheckout, onRefresh, onFetch, onPull, onPush}: Props) {
  const ahead = meta?.ahead ?? 0;
  const behind = meta?.behind ?? 0;
  return (
    <div className="view-section scm-header">
      <div className="scm-branch-row">
        <span className="scm-branch" title={branch ?? undefined}>
          <GitBranch size={16} strokeWidth={1.5} aria-hidden="true" />
          <span className="scm-branch-name">{branch || 'No branch'}</span>
          {(ahead > 0 || behind > 0) && (
            <span className="scm-sync">
              <span className="visually-hidden">
                {ahead} ahead, {behind} behind
              </span>
              <span aria-hidden="true" className="scm-sync-part">
                <ArrowUp size={12} strokeWidth={1.5} />
                {ahead}
              </span>
              <span aria-hidden="true" className="scm-sync-part">
                <ArrowDown size={12} strokeWidth={1.5} />
                {behind}
              </span>
            </span>
          )}
        </span>
        <button type="button" className="scm-icon" aria-label="Refresh source control" title="Refresh" disabled={busy} onClick={onRefresh}>
          <RefreshCw size={16} strokeWidth={1.5} aria-hidden="true" />
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className="scm-icon" aria-label="More source control actions" title="More actions">
              <Ellipsis size={16} strokeWidth={1.5} aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem disabled={busy} onSelect={onFetch}>
              Fetch
            </DropdownMenuItem>
            <DropdownMenuItem disabled={busy} onSelect={onPull}>
              Pull (fast-forward only)
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled={busy || !branch || branch === 'HEAD'} onSelect={onPush}>
              Push
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <label className="scm-field scm-checkout">
        <span>Checkout</span>
        <Select value={currentId} disabled={busy || disableCheckout} onChange={event => onCheckout(event.target.value)}>
          <option value="">Project checkout</option>
          {(meta?.worktrees ?? []).map(tree => (
            <option key={tree.id} value={tree.id}>
              {tree.branch}
            </option>
          ))}
        </Select>
      </label>
    </div>
  );
}
