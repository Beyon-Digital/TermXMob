import {useCallback, useEffect, useRef, useState} from 'react';
import {Alert} from '../../components/ui/alert';
import {Button} from '../../components/ui/button';
import {errorMessage} from '../../lib/characters';

type Task = () => Promise<unknown>;
interface Failure {
  message: string;
  task: Task | null;
}

export interface RunOptions {
  /** Offer a Retry button after a failure. Leave on for idempotent work; turn off for billed requests. Defaults to true. */
  retry?: boolean;
  /** Skip the call while another one from this action is still running, instead of queueing it. */
  exclusive?: boolean;
}

export interface Action {
  /** True while any call is queued or running. Show progress with it; never use it to disable the control. */
  pending: boolean;
  error: string;
  /** Calls run one at a time in call order, so rapid toggles apply in order. Resolves to whether the task succeeded. */
  run: (task: Task, options?: RunOptions) => Promise<boolean>;
  retry?: () => void;
  clear: () => void;
  /** False once the owning component has unmounted. Tasks check it before they send something irreversible. */
  mounted: () => boolean;
}

/** Per-section async state. Errors stay with the section that failed. */
export function useAction(): Action {
  const [count, setCount] = useState(0);
  const [failure, setFailure] = useState<Failure | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const active = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback((task: Task, {retry = true, exclusive = false}: RunOptions = {}) => {
    if (exclusive && active.current > 0) return Promise.resolve(false);
    active.current++;
    setFailure(null);
    setCount(value => value + 1);
    const attempt = queue.current.then(async () => {
      try {
        await task();
        return true;
      } catch (reason) {
        if (alive.current) setFailure({message: errorMessage(reason), task: retry ? task : null});
        return false;
      } finally {
        active.current--;
        if (alive.current) setCount(value => value - 1);
      }
    });
    queue.current = attempt;
    return attempt;
  }, []);

  const failedTask = failure?.task;
  const retry = failedTask ? () => void run(failedTask) : undefined;
  const clear = useCallback(() => setFailure(null), []);
  const mounted = useCallback(() => alive.current, []);
  return {pending: count > 0, error: failure?.message ?? '', run, retry, clear, mounted};
}

/** A message with a Retry button when the failed work is safe to repeat. */
export function ErrorAlert({message, onRetry, className}: {message: string; onRetry?: () => void; className?: string}) {
  return (
    <Alert role="alert" className={'character-alert' + (className ? ' ' + className : '')}>
      <span>{message}</span>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      )}
    </Alert>
  );
}

/** The error beside the section that failed, with Retry when the failed call is safe to repeat. */
export function ActionError({action, className}: {action: Pick<Action, 'error' | 'retry'>; className?: string}) {
  return action.error ? <ErrorAlert message={action.error} onRetry={action.retry} className={className} /> : null;
}
