import {json, request} from './api';

/** A saved site as `/api/browser/sites` and the `browser_site_events` subscription report it. */
export interface Site {
  id: string;
  project_id: string;
  origin: string;
  start_url: string;
  title: string;
  profile_id: string;
  open_mode: 'tab' | 'app';
  pinned: boolean;
  position: number;
  visit_count: number;
  last_visited: number | null;
  frecency: number;
  tab_id: string | null;
  note_count: number;
  created?: number;
  updated?: number;
}

/** An origin visited often but not saved. Offered with a Pin action. */
export interface SiteSuggestion {
  profile_id: string;
  origin: string;
  start_url: string;
  title: string;
  project_id: string;
  visit_count: number;
  last_visited: number | null;
  frecency: number;
}

export interface SiteNote {
  id: string;
  site_id: string;
  author: 'user' | 'agent';
  content: string;
  created: number;
  updated: number;
}

export interface SiteEvent {
  type: string;
  id: string | null;
  site: Site | null;
}

export interface BrowserProfile {
  id: string;
  name: string;
  project_id: string;
  ephemeral: boolean;
}

export interface BrowserTabInfo {
  id: string;
  title: string;
  url: string;
  state: string;
  profile_id: string;
  project_id: string;
}

export const NOTE_LIMIT = 2000;

export const SITE_EVENTS_QUERY =
  'subscription{browser_site_events{type id site{id project_id origin start_url title profile_id open_mode pinned position visit_count last_visited frecency tab_id note_count}}}';

const base = '/api/browser/sites';

export const listSites = () => request<Site[]>(base);
export const listSuggestions = (limit = 5) => request<SiteSuggestion[]>(base + '/suggestions?limit=' + limit);
export const listProfiles = () => request<BrowserProfile[]>('/api/browser/profiles');
export const listBrowserTabs = () => request<BrowserTabInfo[]>('/api/browser/tabs');
export const createProfile = (projectId: string, name: string) =>
  request<BrowserProfile>('/api/browser/profiles', json('POST', {project_id: projectId, name, ephemeral: false}));

/** The profile a new saved site goes in: a saved site cannot live on a temporary profile, and the project's own profile wins. */
export function defaultProfile(profiles: readonly BrowserProfile[], projectId: string): BrowserProfile | undefined {
  const lasting = profiles.filter(profile => !profile.ephemeral);
  return lasting.find(profile => profile.project_id === projectId) ?? lasting[0];
}

export function saveSite(input: {profile_id: string; url: string; title?: string; open_mode?: 'tab' | 'app'; pinned?: boolean}) {
  return request<Site>(base, json('POST', input));
}

export function updateSite(id: string, patch: Partial<Pick<Site, 'title' | 'open_mode' | 'pinned' | 'position'>>) {
  return request<Site>(base + '/' + encodeURIComponent(id), json('PATCH', patch));
}

export function removeSite(id: string) {
  return request<{id: string}>(base + '/' + encodeURIComponent(id), {method: 'DELETE'});
}

/** Opens the site in its profile. The host reuses a live tab; the tab it returns is human-owned. */
export function openSite(id: string) {
  return request<{site: Site; tab: BrowserTabInfo; reused: boolean}>(base + '/' + encodeURIComponent(id) + '/open', json('POST', {}));
}

export const listNotes = (id: string) => request<SiteNote[]>(base + '/' + encodeURIComponent(id) + '/notes');

export function addNote(id: string, content: string) {
  return request<SiteNote>(base + '/' + encodeURIComponent(id) + '/notes', json('POST', {content}));
}

export function deleteNote(id: string, noteId: string) {
  return request<{id: string}>(base + '/' + encodeURIComponent(id) + '/notes?note_id=' + encodeURIComponent(noteId), {method: 'DELETE'});
}

/** Pinned sites in the owner's order; ties fall back to the title so the order is stable. */
export function pinnedSites(sites: readonly Site[]): Site[] {
  return sites.filter(site => site.pinned).sort((a, b) => a.position - b.position || a.title.localeCompare(b.title) || a.id.localeCompare(b.id));
}

/** Saved sites that are not pinned, most used first. */
export function otherSites(sites: readonly Site[]): Site[] {
  return sites.filter(site => !site.pinned).sort((a, b) => b.frecency - a.frecency || a.title.localeCompare(b.title));
}

/** Fold one subscription event into the list. Unknown event types are ignored. */
export function applySiteEvent(sites: readonly Site[], event: SiteEvent): Site[] {
  if (event.type === 'browser.site.removed') return sites.filter(site => site.id !== event.id);
  const site = event.site;
  if (!site) return sites as Site[];
  const known = sites.some(row => row.id === site.id);
  return known ? sites.map(row => (row.id === site.id ? site : row)) : [...sites, site];
}

/**
 * The positions to write after moving the site at `from` to `to` within the pinned list. The host stores absolute positions
 * and never shifts the others, so every row whose stored position is not its new index is written.
 */
export function reorderPins(pins: readonly Site[], from: number, to: number): {id: string; position: number}[] {
  if (from === to || from < 0 || to < 0 || from >= pins.length || to >= pins.length) return [];
  const next = [...pins];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next.flatMap((site, index) => (site.position === index ? [] : [{id: site.id, position: index}]));
}

/** The host name of a site, for the secondary line (`origin` without the scheme). */
export function originLabel(origin: string): string {
  return origin.replace(/^https?:\/\//, '');
}
