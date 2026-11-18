import {createContext, useCallback, useContext, useMemo, useState, type ReactNode} from 'react';

/**
 * The shell's one error message. It shows inline on the sign-in screen and as a dismissible toast once signed in,
 * so it lives above both. `useReport` is stable across renders; `useErrorMessage` changes with the message.
 */
type Report = (reason: unknown) => void;
type ErrorMessage = {error: string; clearError: () => void};

const ReportContext = createContext<Report | null>(null);
const MessageContext = createContext<ErrorMessage | null>(null);

export function ErrorProvider({children}: {children: ReactNode}) {
  const [error, setError] = useState('');
  const report = useCallback<Report>(reason => setError(reason instanceof Error ? reason.message : String(reason)), []);
  const clearError = useCallback(() => setError(''), []);
  const message = useMemo(() => ({error, clearError}), [error, clearError]);
  return (
    <ReportContext.Provider value={report}>
      <MessageContext.Provider value={message}>{children}</MessageContext.Provider>
    </ReportContext.Provider>
  );
}

export function useReport(): Report {
  const report = useContext(ReportContext);
  if (!report) throw new Error('useReport needs an ErrorProvider');
  return report;
}

export function useErrorMessage(): ErrorMessage {
  const message = useContext(MessageContext);
  if (!message) throw new Error('useErrorMessage needs an ErrorProvider');
  return message;
}
