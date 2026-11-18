import {CircleCheck, CircleMinus, CircleX, Clock, LoaderCircle, ShieldAlert, Unplug, type LucideIcon} from 'lucide-react';
import {statusPresentation} from '../../lib/cli-sessions';

const ICON: Record<string, LucideIcon> = {
  starting: LoaderCircle,
  working: LoaderCircle,
  waiting_input: Clock,
  needs_approval: ShieldAlert,
  idle: CircleMinus,
  done: CircleCheck,
  failed: CircleX,
  lost: Unplug,
};

type Props = {
  status: string;
  /** Show the words next to the icon. They are always in the accessible name; the list hides them only when it has no room. */
  compact?: boolean;
};

/**
 * A CLI session's status: an icon, the words and a tone. The tone is never the only signal: working spins, needs approval
 * is a shield, failed is a cross, and the label is always present for a screen reader.
 */
export default function CliStatusBadge({status, compact = false}: Props) {
  const {label, tone} = statusPresentation(status);
  const Icon = ICON[status] ?? CircleMinus;
  const spins = status === 'working' || status === 'starting';
  return (
    <span className="cli-status" data-tone={tone} data-status={status}>
      <Icon className={spins ? 'cli-spin' : undefined} size={16} strokeWidth={1.5} aria-hidden="true" />
      <span className={compact ? 'visually-hidden' : undefined}>{label}</span>
    </span>
  );
}
