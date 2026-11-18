import {Plus} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {needsYouCount, type NeedsYouItem} from '../../lib/bot-schedules';
import {useSidebarSprite} from '../../lib/sidebar-sprite';
import AgentSidebar, {type SidebarState} from '../AgentSidebar';
import type {Coordination} from '../use-coordination';
import MainAgentSprite from './MainAgentSprite';
import './views.css';

type Props = {
  sidebar: SidebarState;
  primaryId: string;
  /** The main agent's conversation is the one in view. */
  mainSelected: boolean;
  /** Where Main agent stands, in words: working, idle, needs approval, unavailable. */
  mainStatus: string;
  creating: boolean;
  coordination: Coordination;
  needs: readonly NeedsYouItem[];
  manager: string;
  onNewChat: () => void;
  onOpenMainAgent: () => void;
  onOpenConversation: (id: string) => void;
  onFindConversation: () => void;
  /** Answering an item happens in Settings > Bots, where the full request is shown. */
  onReviewNeeds: () => void;
};

/**
 * The Agents side bar view: the one primary action (New chat), what needs the owner, the Main agent, its sub-agents, CLI
 * sessions and the chat list. The sub-agents are drawn by the orchestrator into the target below, as before.
 */
export default function AgentsView(props: Props) {
  const {coordination, needs} = props;
  const sprite = useSidebarSprite();
  const attention = coordination.summary.attention;
  return (
    <div className="agents-view">
      <div className="view-section view-section-first">
        <Button className="view-primary" disabled={props.creating} aria-busy={props.creating} onClick={props.onNewChat}>
          <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
          New chat
        </Button>
      </div>

      {needs.length > 0 && (
        <section className="view-section" aria-labelledby="agents-needs">
          <h3 id="agents-needs" className="view-heading">
            Needs you
            <span className="view-count">{needsYouCount(needs.length)}</span>
          </h3>
          <ul className="view-list">
            {needs.slice(0, 5).map(item => (
              <li key={item.id} className="needs-row">
                <span className="needs-title">{item.title}</span>
                <small>{item.bot_name}</small>
              </li>
            ))}
          </ul>
          <Button variant="ghost" size="sm" className="view-link" onClick={props.onReviewNeeds}>
            {needs.length > 5 ? 'Review all ' + needs.length : 'Review'}
          </Button>
        </section>
      )}

      <section className="view-section" aria-labelledby="agents-main">
        <h3 id="agents-main" className="visually-hidden">
          Main agent
        </h3>
        <button
          type="button"
          className="view-row main-agent-row"
          aria-current={props.mainSelected ? 'page' : undefined}
          data-selected={props.mainSelected || undefined}
          onClick={props.onOpenMainAgent}
        >
          {sprite && <MainAgentSprite primaryId={props.primaryId} />}
          <span className="view-row-title">Main agent</span>
          <span className="view-row-meta">{props.mainStatus}</span>
        </button>
      </section>

      <section className="view-section" aria-labelledby="agents-sub">
        <h3 id="agents-sub" className="view-heading">
          Sub-agents
          {attention > 0 && <span className="view-count">{needsYouCount(attention)}</span>}
        </h3>
        <div className="subagents-target" ref={coordination.setTarget} />
      </section>

      <section className="view-section" aria-labelledby="agents-cli">
        <h3 id="agents-cli" className="view-heading">
          CLI sessions
        </h3>
        <p className="view-empty">Claude Code, Codex and other CLI sessions on this host will appear here.</p>
      </section>

      <section className="view-section agents-chats" aria-label="Chats">
        <AgentSidebar
          listOnly
          sidebar={props.sidebar}
          primaryId={props.primaryId}
          manager={props.manager}
          creating={props.creating}
          onNewChat={props.onNewChat}
          onOpenMainAgent={props.onOpenMainAgent}
          onOpenConversation={props.onOpenConversation}
          onFindConversation={props.onFindConversation}
        />
      </section>
    </div>
  );
}
