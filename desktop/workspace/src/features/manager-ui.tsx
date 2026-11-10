import {useState, type ComponentProps, type ReactNode} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {settingsCategories,isSettingsCategory,settingsCategoryDescription} from '../lib/settings-categories';

export const date = (value?: number) => value ? new Date(value * 1000).toLocaleString() : '—';

/** Fields the search box matches across every listed record. */
export interface Searchable { id?: string; name?: string; label?: string; content?: string; event?: string; prompt?: string; success_criteria?: string; device_name?: string | null; display_name?: string; state?: string; status?: string; strength?: string; spec?: {kind?: string; time?: string; timezone?: string}; next_run?: number; expires?: number; revoked?: boolean; enabled?: boolean | null }

export function rowMatcher(search: string, currentSessionId?: string) {
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return (row: Searchable) => {
    const text = [row.id, row.name, row.label, row.content, row.event, row.prompt, row.success_criteria, row.device_name, row.display_name, row.state, row.status, row.strength, row.spec?.kind, row.spec?.time, row.spec?.timezone, row.next_run ? date(row.next_run) : '', row.expires ? date(row.expires) : '', row.revoked ? 'revoked' : '', row.id === currentSessionId ? 'this session' : '', row.enabled === true ? 'enabled' : row.enabled === false ? 'paused' : ''].filter(Boolean).join(' ').toLowerCase();
    return terms.every(term => text.includes(term));
  };
}

export function Json({value}: {value: unknown}) { return <details><summary>Advanced · exact configuration</summary><pre className="manager-json">{JSON.stringify(value, null, 2)}</pre></details> }
export function Empty({children}: {children: ReactNode}) { return <div className="manager-empty">{children}</div> }
export function Item({title, meta, children}: {title: string; meta?: ReactNode; children?: ReactNode}) { return <article className="manager-item"><div><h4>{title}</h4>{meta && <p>{meta}</p>}</div><div className="manager-item-actions">{children}</div></article> }

/** Mounts its body on first open so closed disclosures cost no requests. */
export function Disclosure({title, children}: {title: string; children: ReactNode}) {
  const [visited, setVisited] = useState(false);
  return <details className="manager-disclosure" onToggle={event => { if (event.currentTarget.open) setVisited(true) }}><summary>{title}</summary>{visited && <div className="manager-disclosure-body">{children}</div>}</details>;
}

/** A section's button. `busy` is the section's in-flight write; it is secondary unless `primary` is set. */
export function ActionButton({busy, primary = false, ...props}: ComponentProps<typeof Button> & {busy: boolean; primary?: boolean}) {
  return <Button disabled={busy || props.disabled} variant={primary ? 'default' : 'secondary'} {...props}/>;
}

/** The status region stays mounted so a change of notice is announced; failures use an alert. */
export function ManagerStatus({error, notice, onRetry}: {error: string; notice: string; onRetry: () => void}) {
  return <>
    {error && <div role="alert" className="manager-error">{error}<Button variant="ghost" size="sm" onClick={onRetry}>Retry</Button></div>}
    <div role="status" className="manager-notice">{notice}</div>
  </>;
}

export function ManagerSearch({label, value, onChange}: {label: string; value: string; onChange: (value: string) => void}) {
  return <Input className="manager-search" placeholder={`Search ${label.toLowerCase()}…`} aria-label="Search settings" data-settings-filter value={value} onChange={event => onChange(event.target.value)}/>;
}

export interface ManagerPageLoad { data: unknown; loading: boolean; busy: boolean; error: string; notice: string; reload: () => void }
export interface ManagerPageSearch { label: string; value: string; onChange: (value: string) => void; count: number }

/**
 * The page every settings category shares: its name, what it covers and a Refresh button. A section
 * that loads data passes `load`, so the page also owns the status region and the loading placeholder,
 * and `search`, which appears once there is a query or enough rows to need one.
 */
export function ManagerPage({section, projectId, disabled, onRefresh, load, search, children}: {section: string; projectId: string | null; disabled?: boolean; onRefresh?: () => void; load?: ManagerPageLoad; search?: ManagerPageSearch; children: ReactNode}) {
  const title = settingsCategories.find(row => row.id === section)?.label || 'Workspace settings';
  const description = projectId ? 'Settings for this project and your account.' : isSettingsCategory(section) ? settingsCategoryDescription(section) : 'Manage capabilities, permissions and continuity.';
  return <div className="manager-main">
    <header className="manager-heading"><div><h2>{title}</h2><p>{description}</p></div><Button variant="secondary" disabled={load ? load.loading || load.busy : disabled} onClick={load ? load.reload : onRefresh}>Refresh</Button></header>
    {search && (search.value || search.count >= 8) && <ManagerSearch label={search.label} value={search.value} onChange={search.onChange}/>}
    {load && <ManagerStatus error={load.error} notice={load.notice} onRetry={load.reload}/>}
    {load && load.loading && !load.data ? <Empty>Loading current settings…</Empty> : children}
  </div>;
}
