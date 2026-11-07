/** Plain-language sign-in state for an engine; never show the raw backend value. */
export function engineAuthLabel(state?: string | null) {
  switch (state) {
    case 'authenticated': return 'Signed in';
    case 'unauthenticated': return 'Sign-in required';
    case 'expired': return 'Sign-in expired';
    case 'not_required': return 'No sign-in needed';
    default: return 'Sign-in not checked';
  }
}
