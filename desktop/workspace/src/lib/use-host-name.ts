import {useEffect, useState} from 'react';
import {gql} from './api';

/** The name of the machine the workspace is connected to, for the status bar. Empty until the host answers. */
export function useHostName(enabled = true): string {
  const [name, setName] = useState('');
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void gql<{machine: {hostname: string}}>('{ machine { hostname } }')
      .then(value => {
        if (live && value.machine?.hostname) setName(value.machine.hostname);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [enabled]);
  return name;
}
