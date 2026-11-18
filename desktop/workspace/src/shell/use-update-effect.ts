import {useEffect, useRef, type DependencyList, type EffectCallback} from 'react';

/** Like `useEffect`, but not on mount: it reacts to later changes of `deps` only. */
export function useUpdateEffect(effect: EffectCallback, deps: DependencyList) {
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    return effect();
  }, deps);
}
