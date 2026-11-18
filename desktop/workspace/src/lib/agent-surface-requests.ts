/**
 * Commands that act on the agent surfaces (CLI sessions, Sites, host health). The command registry runs above the frame that
 * owns those surfaces' state, so a command only raises a request here and the frame answers it.
 */

export type AgentSurfaceRequest = 'cli.new' | 'cli.focus' | 'sites.addCurrent' | 'host.status';

export const AGENT_SURFACE_EVENT = 'termx-agent-surface';

export function requestAgentSurface(request: AgentSurfaceRequest) {
  window.dispatchEvent(new CustomEvent<AgentSurfaceRequest>(AGENT_SURFACE_EVENT, {detail: request}));
}

/** Listen for requests. Returns the function that stops listening. */
export function onAgentSurfaceRequest(handler: (request: AgentSurfaceRequest) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<AgentSurfaceRequest>).detail);
  window.addEventListener(AGENT_SURFACE_EVENT, listener);
  return () => window.removeEventListener(AGENT_SURFACE_EVENT, listener);
}
