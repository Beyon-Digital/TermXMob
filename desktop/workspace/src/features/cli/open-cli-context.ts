import {createContext, useContext} from 'react';

/**
 * Show a CLI session in a tab. The new layout provides it; surfaces that can name a session (the task board's CLI nodes)
 * offer "Open CLI session" only when it is there, because the old dock has no CLI tab.
 */
export const OpenCliSessionContext = createContext<((sessionId: string) => void) | null>(null);

export const useOpenCliSession = () => useContext(OpenCliSessionContext);
