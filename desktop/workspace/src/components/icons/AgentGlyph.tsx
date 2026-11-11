import {useId, type CSSProperties} from 'react';
import './workspace-icons.css';

type Props = {seed: string; size?: number; className?: string; title?: string};
function identity(seed: string) {
  let hash = 2166136261;
  for (const point of seed) hash = Math.imul(hash ^ point.codePointAt(0)!, 16777619);
  return {shape: (hash >>> 0) % 8, palette: ((hash >>> 8) ^ (hash >>> 16)) % 8};
}

/** Decorative, stable identity for a real agent. Text carries its name and status. */
export function AgentGlyph({seed, size = 24, className = '', title}: Props) {
  const id = useId(), {shape, palette} = identity(seed);
  const petals = [0, 45, 90, 135, 180, 225, 270, 315];
  return <svg className={'agent-glyph ' + className} width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden={title ? undefined : true} role={title ? 'img' : undefined} aria-labelledby={title ? id : undefined} focusable="false" data-shape={shape} style={{'--glyph-color': `var(--agent-glyph-${palette})`} as CSSProperties}>
    {title && <title id={id}>{title}</title>}
    {shape === 0 && <g fill="currentColor">{petals.map((angle, i) => <path key={angle} d="M12 2 15 8 12 11 9 8Z" transform={`rotate(${angle} 12 12)`} opacity={i % 2 ? .6 : 1}/>)}<circle cx="12" cy="12" r="2.1"/></g>}
    {shape === 1 && <g fill="currentColor">{[0, 90, 180, 270].map((angle, i) => <ellipse key={angle} cx="12" cy="7.5" rx="4.1" ry="5.3" transform={`rotate(${angle + 25} 12 12)`} opacity={.55 + i * .13}/>)}<circle cx="12" cy="12" r="2"/></g>}
    {shape === 2 && <g fill="currentColor"><path d="m12 2 7 4-7 4-7-4Z"/><path d="m5 8 6 3.5V19l-6-3.5Z" opacity=".65"/><path d="m19 8-6 3.5V19l6-3.5Z" opacity=".85"/><path d="m4 17 7 4-3 1.5-6-3.5Zm16 0-7 4 3 1.5 6-3.5Z" opacity=".45"/></g>}
    {shape === 3 && <g fill="currentColor">{[0, 90, 180, 270].map((angle, i) => <path key={angle} d="M12 2c6 0 10 4 10 10H12Z" transform={`rotate(${angle} 12 12)`} opacity={.45 + i * .18}/>)}</g>}
    {shape === 4 && <g fill="currentColor"><path d="m12 2 5 5-5 5-5-5Z"/><path d="m7 7 5 5-5 5-5-5Z" opacity=".65"/><path d="m17 7 5 5-5 5-5-5Z" opacity=".65"/><path d="m12 12 5 5-5 5-5-5Z" opacity=".85"/></g>}
    {shape === 5 && <g fill="currentColor"><path d="M3 3h18l-9 18Z" opacity=".55"/><path d="M3 21h18L12 3Z" opacity=".75"/><path d="m12 8 4 7H8Z"/></g>}
    {shape === 6 && <g fill="currentColor">{[0, 60, 120, 180, 240, 300].map((angle, i) => <circle key={angle} cx="12" cy="6.6" r="4" transform={`rotate(${angle} 12 12)`} opacity={i % 2 ? .6 : .9}/>)}<circle cx="12" cy="12" r="3"/></g>}
    {shape === 7 && <g stroke="currentColor" strokeWidth="3.4" strokeLinecap="round"><path d="M7 4v16M17 4v16M4 8h16M4 16h16"/><path d="M7 8h10v8H7Z" opacity=".5"/></g>}
  </svg>;
}
