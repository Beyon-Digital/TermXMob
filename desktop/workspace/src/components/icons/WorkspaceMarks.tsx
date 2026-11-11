import type {SVGProps} from 'react';
import './workspace-icons.css';
type Props = SVGProps<SVGSVGElement> & {size?: number};

/** TermX's original studio mark: a frame, a lens and a small motion cue. */
export function StudioMark({size = 20, ...props}: Props) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}><rect x="3" y="4" width="18" height="16" rx="4"/><path d="m6 16 4-4 3 3 2-2 3 3M7 4l2-2h6l2 2"/><circle cx="15.7" cy="8.7" r="1.5"/></svg>;
}

/** A compact conversation mark with an open-ended cursor. */
export function AgentMark({size = 20, ...props}: Props) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}><path d="M6 4h12a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8l-5 3V7a3 3 0 0 1 3-3Z"/><path d="m8 9 3 3-3 3m6 0h3"/></svg>;
}

/** Original music mark: a pair of notes within a small sound field. */
export function MusicMark({size = 20, ...props}: Props) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}><path d="M10 17V6l9-2v11M10 9l9-2"/><ellipse cx="7.5" cy="17.5" rx="2.5" ry="2"/><ellipse cx="16.5" cy="15.5" rx="2.5" ry="2"/><path d="M3 7v4M5.5 5.5v7"/></svg>;
}

/** Original data mark: layered records with an analytical signal. */
export function DataMark({size = 20, ...props}: Props) {
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}><ellipse cx="10" cy="5" rx="7" ry="3"/><path d="M3 5v11c0 1.7 3.1 3 7 3M3 10c0 1.7 3.1 3 7 3m7-8v5m-4 11v-4m4 4v-7m4 7v-10"/></svg>;
}
