import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {useState} from 'react';
import MediaWorkspace from './MediaWorkspace';

vi.mock('./MediaStudio', () => ({default: ({visible, catalogRevision, onOpenArtifacts, onConfigureProvider}: {visible: boolean; catalogRevision: number; onOpenArtifacts: () => void; onConfigureProvider: () => void}) => <div><output aria-label="Studio visibility">{String(visible)}</output><output aria-label="Catalog revision">{catalogRevision}</output><textarea aria-label="Retained Studio prompt" defaultValue=""/><button onClick={onConfigureProvider}>Configure provider</button><button data-open-artifacts onClick={onOpenArtifacts}>Open artifact editor</button></div>}));
vi.mock('./MusicStudio', () => ({default: ({visible, locked}: {visible: boolean; locked: boolean}) => <div><output aria-label="Music visibility">{String(visible)}</output><output aria-label="Music lock">{String(locked)}</output><textarea aria-label="Retained music draft" defaultValue=""/></div>}));
vi.mock('./StudioSettings', () => ({default: ({visible, observing, locked, onSaved, onProvidersChanged}: {visible: boolean; observing: boolean; locked: boolean; onSaved: () => void; onProvidersChanged: () => void}) => {
  const [draft, setDraft] = useState('');
  return <section aria-label="Studio Settings fixture" data-visible={visible} data-observing={observing} data-locked={locked}><textarea aria-label="Settings draft" value={draft} onChange={event => setDraft(event.target.value)}/><button onClick={onSaved}>Save Studio settings</button><button onClick={onProvidersChanged}>Change Studio providers</button></section>;
}}));
vi.mock('./Media', () => ({default: ({visible}: {visible: boolean}) => {
  const [draft, setDraft] = useState('');
  return <div><output aria-label="Artifact visibility">{String(visible)}</output><textarea aria-label="Retained document draft" value={draft} onChange={event => setDraft(event.target.value)}/></div>;
}}));
afterEach(cleanup);

it('opens the studio without starting the artifact editor', async () => {
  render(<MediaWorkspace projectId="project" ownerId="owner"/>);
  expect(await screen.findByLabelText('Studio visibility')).toHaveTextContent('true');
  expect(screen.getByRole('tab', {name: 'Media studio'})).toHaveAttribute('aria-selected', 'true');
  expect(screen.queryByLabelText('Retained document draft')).not.toBeInTheDocument();
});

it('retains an unsaved artifact edit while visiting the studio and fences hidden surfaces', async () => {
  const view = render(<MediaWorkspace projectId="project" ownerId="owner"/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Open artifact editor'}));
  fireEvent.change(await screen.findByLabelText('Retained document draft'), {target: {value: 'Keep this unsaved text'}});
  expect(screen.getByLabelText('Studio visibility')).toHaveTextContent('false');
  fireEvent.click(screen.getByRole('tab', {name: 'Media studio'}));
  expect(screen.getByLabelText('Artifact visibility')).toHaveTextContent('false');
  fireEvent.click(screen.getByRole('tab', {name: 'Artifacts & documents'}));
  expect(screen.getByLabelText('Retained document draft')).toHaveValue('Keep this unsaved text');
  view.rerender(<MediaWorkspace projectId="project" ownerId="owner" visible={false}/>);
  expect(screen.getByLabelText('Artifact visibility')).toHaveTextContent('false');
  expect(screen.getByLabelText('Studio visibility')).toHaveTextContent('false');
});

it('moves keyboard focus with tab selection and uses unique panel identities', async () => {
  render(<MediaWorkspace projectId={null}/>);
  const studio = screen.getByRole('tab', {name: 'Media studio'});
  studio.focus();
  fireEvent.keyDown(studio, {key: 'ArrowRight'});
  const artifacts = screen.getByRole('tab', {name: 'Artifacts & documents'});
  expect(artifacts).toHaveFocus();
  expect(artifacts).toHaveAttribute('aria-selected', 'true');
  await screen.findByLabelText('Retained document draft');
  fireEvent.keyDown(artifacts, {key: 'Home'});
  expect(studio).toHaveFocus();
  expect(studio).toHaveAttribute('aria-selected', 'true');
  expect(document.getElementById(studio.getAttribute('aria-controls')!)).toHaveAttribute('aria-labelledby', studio.id);
});


it('forwards standalone sidebar sections without a second media navigation layer', async () => {
  const onSectionChange = vi.fn();
  render(<MediaWorkspace projectId="project" ownerId="owner" presentation="standalone" section="library" onSectionChange={onSectionChange}/>);
  expect(await screen.findByLabelText('Studio visibility')).toHaveTextContent('true');
  expect(screen.queryByRole('tablist', {name: 'Media workspace views'})).not.toBeInTheDocument();
  expect(screen.getByRole('region', {name: 'Studio tools'})).toBeVisible();
  expect(screen.queryByLabelText('Retained document draft')).not.toBeInTheDocument();
});

it('keeps the composer and settings drafts while fencing inactive sections and refreshing the catalog', async () => {
  const view = render(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="create"/>);
  const prompt = await screen.findByLabelText('Retained Studio prompt');
  fireEvent.change(prompt, {target: {value: 'Keep my scene'}});
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="settings"/>);
  const settings = await screen.findByRole('region', {name: 'Studio Settings fixture'});
  const draft = screen.getByLabelText('Settings draft');
  fireEvent.change(draft, {target: {value: 'Unsaved settings'}});
  expect(prompt).not.toBeVisible(); expect(prompt.closest('[inert]')).not.toBeNull();
  fireEvent.click(screen.getByRole('button', {name: 'Save Studio settings'}));
  expect(screen.getByLabelText('Catalog revision')).toHaveTextContent('1');
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="create"/>);
  expect(screen.getByLabelText('Retained Studio prompt')).toBe(prompt); expect(prompt).toHaveValue('Keep my scene');
  expect(settings).not.toBeVisible(); expect(settings.closest('[inert]')).not.toBeNull();
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="settings" observing={false}/>);
  expect(screen.getByLabelText('Settings draft')).toBe(draft); expect(draft).toHaveValue('Unsaved settings');
  expect(settings).toBeVisible(); expect(settings).toHaveAttribute('data-observing', 'false');
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="settings" locked/>);
  expect(settings).toHaveAttribute('data-locked', 'true'); expect(settings).not.toBeVisible();
});

it('opens Studio Settings from Configure provider without using global Settings', async () => {
  const onSectionChange = vi.fn();
  render(<MediaWorkspace ownerId="owner" projectId={null} onSectionChange={onSectionChange}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Configure provider'}));
  expect(await screen.findByRole('region', {name: 'Studio Settings fixture'})).toBeVisible();
  expect(onSectionChange).toHaveBeenCalledWith('settings');
});

it('refreshes the shared provider catalog only when Studio providers change', async () => {
  const onProvidersChanged = vi.fn();
  render(<MediaWorkspace ownerId="owner" projectId={null} section="settings" onProvidersChanged={onProvidersChanged}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Save Studio settings'}));
  expect(screen.getByLabelText('Catalog revision')).toHaveTextContent('1');
  expect(onProvidersChanged).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Change Studio providers'}));
  expect(screen.getByLabelText('Catalog revision')).toHaveTextContent('2');
  expect(onProvidersChanged).toHaveBeenCalledOnce();
});


it('retains independent music and visual drafts with hidden and locked fencing', async () => {
  const view = render(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="create"/>);
  const visual = await screen.findByLabelText('Retained Studio prompt');
  fireEvent.change(visual, {target: {value: 'Visual scene'}});
  expect(screen.queryByLabelText('Retained music draft')).not.toBeInTheDocument();
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="music"/>);
  const music = await screen.findByLabelText('Retained music draft');
  fireEvent.change(music, {target: {value: 'Quiet instrumental'}});
  expect(screen.getByLabelText('Music visibility')).toHaveTextContent('true');
  expect(visual).not.toBeVisible(); expect(visual.closest('[inert]')).not.toBeNull();
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="create"/>);
  expect(visual).toHaveValue('Visual scene'); expect(visual).toBeVisible();
  expect(music).toHaveValue('Quiet instrumental'); expect(music).not.toBeVisible();
  expect(music.closest('[inert]')).not.toBeNull();
  view.rerender(<MediaWorkspace ownerId="owner" projectId="project" presentation="standalone" section="music" locked/>);
  expect(screen.getByLabelText('Music visibility')).toHaveTextContent('false');
  expect(screen.getByLabelText('Music lock')).toHaveTextContent('true');
  expect(music).not.toBeVisible();
});
