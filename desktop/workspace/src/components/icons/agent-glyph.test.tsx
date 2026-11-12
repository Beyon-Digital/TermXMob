import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {AgentGlyph} from './AgentGlyph';
afterEach(cleanup);
it('keeps a stable decorative identity when the agent is renamed or rerendered',()=>{
 const view=render(<AgentGlyph seed="session-a"/>), svg=view.container.querySelector('svg')!;
 const initial=svg.outerHTML;
 expect(svg).toHaveAttribute('aria-hidden','true');
 expect(svg).toHaveAttribute('focusable','false');
 view.rerender(<AgentGlyph seed="session-a"/>);
 expect(view.container.querySelector('svg')!.outerHTML).toBe(initial);
});
it('provides an explicit accessible name only when used without adjacent text',()=>{
 render(<AgentGlyph seed="session-b" title="Review agent" size={32}/>);
 expect(screen.getByRole('img',{name:'Review agent'})).toHaveAttribute('width','32');
 expect(screen.getByRole('img',{name:'Review agent'})).not.toHaveAttribute('aria-hidden');
});
