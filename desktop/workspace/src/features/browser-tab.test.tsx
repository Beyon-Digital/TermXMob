import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {request} from '../lib/api';
import {setBrowserTabTitle, useBrowserTabTitles} from '../lib/browser-tab-titles';
import {renderHook} from '@testing-library/react';
import {BrowserSurface} from './BrowserSurface';
import {expectNoAxeViolations} from '../test-axe';

vi.mock('../lib/api', () => ({request: vi.fn()}));
const api = vi.mocked(request);

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  onopen?: () => void;
  onmessage?: (event: {data: string}) => void;
  onerror?: () => void;
  onclose?: (event: CloseEvent) => void;
  binaryType = '';
  url: string;
  constructor(url: string) {
    this.url = url;
    Socket.instances.push(this);
    setTimeout(() => this.onopen?.(), 0);
  }
  send = vi.fn();
  close = vi.fn();
}

const profile = {id: 'profile', name: 'Project account', project_id: 'project', ephemeral: false};
const tab = (over: Record<string, unknown> = {}) => ({
  id: 'tab-a',
  title: 'Docs home',
  url: 'https://docs.example.com/start',
  state: 'human',
  profile_id: 'profile',
  project_id: 'project',
  document_revision: 1,
  lease_revision: 1,
  recording: false,
  grant_id: null,
  ...over,
});

let tabs: ReturnType<typeof tab>[];
let profiles: (typeof profile)[];

beforeEach(() => {
  sessionStorage.clear();
  Socket.instances = [];
  tabs = [tab(), tab({id: 'tab-b', title: 'Mail', url: 'https://mail.example.com/'})];
  profiles = [profile];
  vi.stubGlobal('WebSocket', Socket);
  api.mockReset();
  api.mockImplementation(async (path: string) => {
    if (path === '/api/browser/profiles') return profiles;
    if (path === '/api/browser/tabs') return tabs;
    if (path === '/api/browser/reviews' || path === '/api/browser/downloads') return [];
    if (path === '/api/browser/sites') return {id: 's1', title: 'Docs home', origin: 'https://docs.example.com'};
    return {};
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('a browser tab that shows one built-in tab', () => {
  it('shows only its own tab, with no tab strip, and views that tab\'s stream', async () => {
    render(<BrowserSurface projectId="project" tabId="tab-b" />);
    await waitFor(() => expect(Socket.instances).toHaveLength(1));
    expect(Socket.instances[0].url).toContain('/api/browser/tabs/tab-b/view');
    expect(screen.queryByRole('tablist', {name: 'Built-in browser tabs'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Open built-in tab'})).not.toBeInTheDocument();
    expect((await screen.findByLabelText('Page address')) as HTMLInputElement).toHaveValue('https://mail.example.com/');
  });

  it('still shows the whole browser, with its own tab strip, when no tab is named', async () => {
    render(<BrowserSurface projectId="project" />);
    expect(await screen.findByRole('tablist', {name: 'Built-in browser tabs'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Open built-in tab'})).toBeInTheDocument();
  });

  it('is named after its page for the tab strip', async () => {
    const {result} = renderHook(() => useBrowserTabTitles());
    render(<BrowserSurface projectId="project" tabId="tab-a" />);
    await waitFor(() => expect(result.current.get('tab-a')).toBe('Docs home'));
    act(() => setBrowserTabTitle('tab-a', ''));
    expect(result.current.has('tab-a')).toBe(false);
  });

  it('adds the current page to Sites with the tab\'s own profile', async () => {
    render(<BrowserSurface projectId="project" tabId="tab-a" />);
    fireEvent.click(await screen.findByRole('button', {name: 'Add current page to sites'}));
    await waitFor(() => expect(api.mock.calls.some(([path]) => path === '/api/browser/sites')).toBe(true));
    const [, requestInit] = api.mock.calls.find(([path]) => path === '/api/browser/sites')!;
    expect(requestInit?.method).toBe('POST');
    expect(JSON.parse(String(requestInit?.body))).toEqual({profile_id: 'profile', url: 'https://docs.example.com/start', title: 'Docs home', pinned: true});
    expect(await screen.findByText('Saved Docs home to Sites.')).toBeInTheDocument();
  });

  it('offers Add to sites only for a lasting profile and an http page', async () => {
    profiles = [{...profile, ephemeral: true}];
    const first = render(<BrowserSurface projectId="project" tabId="tab-a" />);
    await screen.findByLabelText('Page address');
    expect(screen.queryByRole('button', {name: 'Add current page to sites'})).not.toBeInTheDocument();
    first.unmount();
    profiles = [profile];
    tabs = [tab({url: 'about:blank'})];
    render(<BrowserSurface projectId="project" tabId="tab-a" />);
    await screen.findByLabelText('Page address');
    expect(screen.queryByRole('button', {name: 'Add current page to sites'})).not.toBeInTheDocument();
  });

  it('saves the page from the Add current page command when it is on screen', async () => {
    render(<BrowserSurface projectId="project" tabId="tab-a" />);
    await screen.findByRole('button', {name: 'Add current page to sites'});
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({width: 400, height: 300, x: 0, y: 0, top: 0, left: 0, right: 400, bottom: 300, toJSON: () => ({})});
    window.dispatchEvent(new Event('termx-site-add-current'));
    await waitFor(() => expect(api.mock.calls.some(([path]) => path === '/api/browser/sites')).toBe(true));
    spy.mockRestore();
  });

  it('says the page was closed, and lets the layout tab go', async () => {
    tabs = [tab({state: 'closed'})];
    const onClose = vi.fn();
    render(<BrowserSurface projectId="project" tabId="tab-a" onClose={onClose} />);
    expect(await screen.findByText('This page was closed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Close tab'}));
    expect(onClose).toHaveBeenCalled();
  });

  it('closes the page it shows, then its layout tab', async () => {
    const onClose = vi.fn();
    render(<BrowserSurface projectId="project" tabId="tab-a" onClose={onClose} />);
    fireEvent.click(await screen.findByRole('button', {name: 'Close page'}));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(api.mock.calls.some(([path, requestInit]) => path === '/api/browser/tabs/tab-a' && requestInit?.method === 'DELETE')).toBe(true);
  });

  it('has no axe violations', async () => {
    const {container} = render(<BrowserSurface projectId="project" tabId="tab-a" />);
    await screen.findByRole('button', {name: 'Add current page to sites'});
    await expectNoAxeViolations(container);
  });
});
