import {useEffect, useRef, type ReactNode} from 'react';
import {X} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../../components/ui/tabs';
import type {DataTab, DataTabsState} from '../../lib/data-tabs';

type Props = {
  state: DataTabsState; title: (tab: DataTab) => string; onSelect: (id: string) => void; onClose: (id: string) => void;
  /** Controls on the right side of the strip. */
  actions?: ReactNode;
  /** Rendered between the strip and the panels, for page-level notices. */
  banner?: ReactNode;
  panel: (tab: DataTab, active: boolean) => ReactNode;
};
/** Closable canvas tabs. Query tabs stay mounted while hidden so unsaved SQL survives a tab switch. */
export default function DataTabs({state, title, onSelect, onClose, actions, banner, panel}: Props) {
  const activeTab = state.tabs.find(tab => tab.id === state.activeId), list = useRef<HTMLDivElement>(null);
  // A narrow strip scrolls sideways, so keep the selected tab in view.
  useEffect(() => {list.current?.querySelector<HTMLElement>('[role=tab][data-state=active]')?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});}, [state.activeId]);
  return <Tabs className="data-tabs" value={state.activeId} onValueChange={onSelect}>
    <header className="data-header">
      <TabsList ref={list} className="data-tab-list" aria-label="Open tables and queries">
        {state.tabs.map(tab => {
          const label = title(tab), closable = tab.kind !== 'overview';
          return <div className="data-tab" key={tab.id} data-closable={closable || undefined}>
            <TabsTrigger value={tab.id} title={label} onKeyDown={event => {if (closable && event.key === 'Delete') {event.preventDefault(); onClose(tab.id);}}}><span className="data-tab-label">{label}</span></TabsTrigger>
            {/* A button inside a tablist breaks the tab pattern, so this pointer shortcut is hidden from assistive technology. Keyboard users press Delete; the toolbar button below closes the active tab. */}
            {closable && <button type="button" className="data-tab-close" aria-hidden="true" tabIndex={-1} onClick={() => onClose(tab.id)}><X size={16}/></button>}
          </div>;
        })}
      </TabsList>
      <div className="data-toolbar">
        {activeTab && activeTab.kind !== 'overview' && <Button variant="ghost" size="icon" aria-label={'Close ' + title(activeTab)} title={'Close ' + title(activeTab)} onClick={() => onClose(activeTab.id)}><X size={16}/></Button>}
        {actions}
      </div>
    </header>
    {banner}
    {state.tabs.map(tab => <TabsContent key={tab.id} value={tab.id} className="data-tab-panel" {...(tab.kind === 'query' ? {forceMount: true as const, hidden: tab.id !== state.activeId} : {})}>{panel(tab, tab.id === state.activeId)}</TabsContent>)}
  </Tabs>;
}
