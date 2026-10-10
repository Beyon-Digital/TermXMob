import {useEffect, useState} from 'react';

/** Observation is owned by a visible panel in a focused foreground window. */
export function useObservationActive(visible = true) {
  const [foreground, setForeground] = useState(() => !document.hidden && document.hasFocus());
  useEffect(() => {
    const update = () => setForeground(!document.hidden && document.hasFocus());
    const blur = () => setForeground(false);
    window.addEventListener('focus', update);
    window.addEventListener('blur', blur);
    document.addEventListener('visibilitychange', update);
    return () => {
      window.removeEventListener('focus', update);
      window.removeEventListener('blur', blur);
      document.removeEventListener('visibilitychange', update);
    };
  }, []);
  return visible && foreground;
}
