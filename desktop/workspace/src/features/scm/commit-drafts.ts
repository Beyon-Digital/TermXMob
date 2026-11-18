/** Commit messages by checkout, so one survives switching to another side bar view and back (the view unmounts). */
const drafts = new Map<string, string>();

export const readDraft = (scope: string) => drafts.get(scope) ?? '';

export function writeDraft(scope: string, message: string) {
  if (message) drafts.set(scope, message);
  else drafts.delete(scope);
}

export const clearDrafts = () => drafts.clear();
