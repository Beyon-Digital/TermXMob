import {X} from 'lucide-react';
import {TabsList, TabsTrigger} from '../../components/ui/tabs';

export type TerminalTab = {id: string; label: string};

/**
 * The session tab strip. Tabs follow the Radix tablist pattern (arrow keys, Home, End).
 * A tablist may only own tabs, so the per-tab close mark is a pointer affordance hidden from
 * assistive technology; the keyboard and screen-reader route is the "Close terminal" button in
 * the toolbar, which acts on the selected tab.
 */
export default function TerminalTabs({tabs, onClose, onActivate, tablistRef}: {tabs: TerminalTab[]; onClose: (id: string) => void; onActivate: (id: string) => void; tablistRef: React.Ref<HTMLDivElement>}) {
  return (
    <TabsList ref={tablistRef} className="terminal-tabs" aria-label="Terminal sessions">
      {tabs.map((tab) => (
        <TabsTrigger key={tab.id} value={tab.id} data-session-id={tab.id} onClick={() => onActivate(tab.id)}>
          <span className="terminal-tab-label">{tab.label}</span>
          <span
            className="terminal-tab-close"
            aria-hidden="true"
            title={'Close ' + tab.label}
            onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
            onClick={(event) => { event.stopPropagation(); onClose(tab.id); }}
          >
            <X size={14} strokeWidth={1.5} />
          </span>
        </TabsTrigger>
      ))}
    </TabsList>
  );
}
