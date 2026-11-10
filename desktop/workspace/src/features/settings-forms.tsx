import {createContext,useCallback,useContext,useEffect,useId,useRef,useState,type RefObject} from 'react';

/** The settings surface listens for this cancelable event before anything navigates away from unsaved edits. */
export const SETTINGS_LEAVE='termx-settings-leave';

/**
 * Ask the settings surface whether it is safe to leave. When edits are unsaved the surface opens its
 * discard dialog and runs `proceed` only after the person confirms; otherwise `proceed` runs now.
 */
export function leaveSettings(proceed: () => void) {
  if (window.dispatchEvent(new CustomEvent(SETTINGS_LEAVE, {cancelable: true, detail: {proceed}}))) proceed();
}

/** Registry the surface provides. Forms report their element while dirty and `null` once clean or unmounted. */
export interface SettingsFormsRegistry { report(key: string, form: RefObject<HTMLFormElement | null> | null): void }
export const SettingsFormsContext = createContext<SettingsFormsRegistry>({report() {}});

/**
 * Tracks one form's unsaved state for the settings surface. Spread `formProps` onto the `<form>`, call
 * `markClean()` after a successful save. The surface's Save button submits the form; Discard remounts it.
 */
export function useDirtyForm(id: string) {
  const registry = useContext(SettingsFormsContext), key = `${id}:${useId()}`;
  const ref = useRef<HTMLFormElement>(null), [dirty, setDirty] = useState(false);
  useEffect(() => { registry.report(key, dirty ? ref : null); return () => registry.report(key, null) }, [registry, key, dirty]);
  const markDirty = useCallback(() => setDirty(true), []), markClean = useCallback(() => setDirty(false), []);
  return {dirty, markClean, formProps: {ref, 'data-settings-form': id, onChange: markDirty, onReset: markClean}};
}
