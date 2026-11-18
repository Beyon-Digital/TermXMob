import {Button} from '../../components/ui/button';
import {Disclosure} from './Disclosure';
import type {ActionRecord} from './scm-types';

type Props = {records: ActionRecord[]; busy: boolean; onInspect: (record: ActionRecord) => void};

/** The host's record of recent delivery operations. One that is waiting can be reviewed; one in doubt is never replayed. */
export default function OperationsSection({records, busy, onInspect}: Props) {
  if (!records.length) return null;
  return (
    <Disclosure title="Recent operations">
      <ul className="scm-operations" aria-label="Recent operations">
        {records.map(record => (
          <li key={record.id}>
            <span>
              {record.operation} · {record.status} · {new Date(record.created * 1000).toLocaleTimeString()}
            </span>
            {record.status === 'prepared' && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => onInspect(record)}>
                Review exact operation
              </Button>
            )}
            {record.status === 'unknown' && <p className="scm-note">Outcome uncertain. Check the repository and the remote before starting a new operation.</p>}
          </li>
        ))}
      </ul>
    </Disclosure>
  );
}
