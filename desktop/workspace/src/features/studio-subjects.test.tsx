import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import StudioSubjects from './StudioSubjects';
import {createStudioSubject, deleteStudioSubject, loadStudioSubjects, type StudioSubject} from '../lib/studio-subjects';
import {uploadStudioImage} from '../lib/media-studio';
import {transfer} from '../lib/transfers';

vi.mock('../lib/studio-subjects', () => ({createStudioSubject: vi.fn(), deleteStudioSubject: vi.fn(), loadStudioSubjects: vi.fn()}));
vi.mock('../lib/media-studio', async original => ({...await original<typeof import('../lib/media-studio')>(), uploadStudioImage: vi.fn()}));
vi.mock('../lib/transfers', () => ({transfer: vi.fn()}));
const subject: StudioSubject = {id: 'saved', project_id: 'project', name: 'Remy', description: 'Blue jacket', image_ids: ['portrait-a', 'portrait-b'], created: 1};
const createURL = vi.fn(), revokeURL = vi.fn();
function photo(name = 'photo.png') {return new File(['pixels'], name, {type: 'image/png'});}
function pick(files = [photo()]) {fireEvent.change(screen.getByLabelText('Reference set photos'), {target: {files}});}
async function open() {fireEvent.click(screen.getByRole('button', {name: 'Saved subjects & products'})); await screen.findByRole('group', {name: 'New reference set'}); await waitFor(() => expect(screen.queryByText('Loading saved reference sets…')).not.toBeInTheDocument());}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadStudioSubjects).mockResolvedValue([]);
  vi.mocked(createStudioSubject).mockResolvedValue({...subject, image_ids: ['uploaded']});
  vi.mocked(deleteStudioSubject).mockResolvedValue();
  vi.mocked(uploadStudioImage).mockResolvedValue({id: 'uploaded', name: 'photo.png'});
  vi.mocked(transfer).mockRejectedValue(Error('No preview'));
  let next = 0; createURL.mockImplementation(() => 'blob:photo-' + (++next));
  Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: createURL});
  Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: revokeURL});
  Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

it('starts collapsed, uses local search, and selects exactly one chosen portrait', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject, {...subject, id: 'mug', name: 'Travel mug', description: 'Red enamel'}]);
  const select = vi.fn(); render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={select}/>);
  expect(loadStudioSubjects).not.toHaveBeenCalled();
  await open();
  expect(loadStudioSubjects).toHaveBeenCalledWith('project', expect.any(AbortSignal));
  expect(screen.getByText(/consistency is not guaranteed/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Find a saved set'), {target: {value: 'jacket'}});
  expect(screen.queryByRole('article', {name: 'Travel mug'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('portrait-b', 'Remy', 'Blue jacket');
  expect(createStudioSubject).not.toHaveBeenCalled();
  expect(uploadStudioImage).not.toHaveBeenCalled();
});

it('opens embedded saved assets without a redundant disclosure and preserves the same draft while hidden', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const select = vi.fn(), selectMany = vi.fn();
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible embedded onSelect={select} onSelectMany={selectMany} maxReferences={5}/>);
  expect(screen.queryByRole('button', {name: 'Saved subjects & products'})).not.toBeInTheDocument();
  await screen.findByRole('button', {name: 'Photo 2 for Remy'});
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'}));
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Embedded draft'}}); pick();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} embedded onSelect={select} onSelectMany={selectMany} maxReferences={5}/>);
  expect(screen.queryByRole('group', {name: 'New reference set'})).not.toBeInTheDocument();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible embedded onSelect={select} onSelectMany={selectMany} maxReferences={5}/>);
  expect(screen.getByLabelText('Name')).toHaveValue('Embedded draft');
  expect(screen.getByRole('img', {name: 'photo.png'})).toHaveAttribute('src', 'blob:photo-2');
  await screen.findByRole('button', {name: 'Photo 2 for Remy'});
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('portrait-b', 'Remy', 'Blue jacket');
  fireEvent.click(screen.getByRole('button', {name: 'Use reference set from Remy'}));
  expect(selectMany).toHaveBeenCalledExactlyOnceWith(['portrait-a', 'portrait-b'], 'Remy', 'Blue jacket');
  expect(uploadStudioImage).not.toHaveBeenCalled();
});


it('keeps the visible saved-reference form and draft while observation pauses, then restores previews without saving', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const select = vi.fn(), original = photo();
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible embedded onSelect={select}/>);
  await screen.findByRole('button', {name: 'Photo 2 for Remy'});
  const nameInput = screen.getByLabelText('Name'), descriptionInput = screen.getByLabelText(/Description/);
  fireEvent.change(nameInput, {target: {value: 'Retained window draft'}});
  fireEvent.change(descriptionInput, {target: {value: 'Original file and selected portrait'}});
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'})); pick([original]);
  fireEvent.click(screen.getByRole('button', {name: 'Remove Remy'}));
  expect(screen.getByRole('group', {name: 'Confirm removal of Remy'})).toBeInTheDocument();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible observing={false} embedded onSelect={select}/>);
  expect(screen.getByLabelText('Name')).toBe(nameInput);
  expect(screen.getByLabelText(/Description/)).toBe(descriptionInput);
  expect(nameInput).toHaveValue('Retained window draft');
  expect(descriptionInput).toHaveValue('Original file and selected portrait');
  expect(screen.getByRole('group', {name: 'New reference set'})).toBeInTheDocument();
  expect(screen.getByText('Add photos (1/5)')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Photo 2 for Remy'})).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('group', {name: 'Confirm removal of Remy'})).not.toBeInTheDocument();
  expect(screen.queryByRole('img', {name: 'photo.png'})).not.toBeInTheDocument();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
  expect(screen.getByRole('button', {name: 'Save reference set'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).not.toHaveBeenCalled();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible embedded onSelect={select}/>);
  expect(screen.getByLabelText('Name')).toBe(nameInput);
  expect(screen.getByRole('img', {name: 'photo.png'})).toHaveAttribute('src', 'blob:photo-2');
  expect(createURL).toHaveBeenNthCalledWith(2, original);
  await waitFor(() => expect(screen.getByRole('button', {name: 'Save reference set'})).toBeEnabled());
  expect(screen.getByRole('button', {name: 'Photo 2 for Remy'})).toHaveAttribute('aria-pressed', 'true');
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(createStudioSubject).not.toHaveBeenCalled(); expect(deleteStudioSubject).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  await screen.findByText('Reference set saved to this workspace.');
  expect(uploadStudioImage).toHaveBeenCalledExactlyOnceWith(original, 'project', expect.any(AbortSignal));
  expect(createStudioSubject).toHaveBeenCalledExactlyOnceWith('project', 'Retained window draft', 'Original file and selected portrait', ['uploaded'], expect.any(AbortSignal));
});

it('fences a late read while unobserved and refreshes only metadata when observation resumes', async () => {
  let finish!: (rows: StudioSubject[]) => void;
  vi.mocked(loadStudioSubjects).mockReturnValueOnce(new Promise(resolve => finish = resolve)).mockResolvedValueOnce([]);
  const view = render(<StudioSubjects projectId="project" visible embedded onSelect={vi.fn()}/>);
  const nameInput = screen.getByLabelText('Name');
  fireEvent.change(nameInput, {target: {value: 'Keep while reading'}}); pick();
  const signal = vi.mocked(loadStudioSubjects).mock.calls[0][1];
  view.rerender(<StudioSubjects projectId="project" visible observing={false} embedded onSelect={vi.fn()}/>);
  expect(signal?.aborted).toBe(true); expect(screen.getByLabelText('Name')).toBe(nameInput);
  await act(async () => finish([subject]));
  expect(screen.queryByRole('article', {name: 'Remy'})).not.toBeInTheDocument();
  expect(loadStudioSubjects).toHaveBeenCalledTimes(1);
  view.rerender(<StudioSubjects projectId="project" visible embedded onSelect={vi.fn()}/>);
  await waitFor(() => expect(loadStudioSubjects).toHaveBeenCalledTimes(2));
  expect(nameInput).toHaveValue('Keep while reading'); expect(screen.getByText('Add photos (1/5)')).toBeInTheDocument();
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(createStudioSubject).not.toHaveBeenCalled();
});

it('uploads sequentially to the workspace then saves only returned IDs and metadata', async () => {
  let finish!: (value: {id: string; name: string}) => void;
  vi.mocked(uploadStudioImage).mockReturnValueOnce(new Promise(resolve => finish = resolve)).mockResolvedValueOnce({id:'second', name:'second.png'});
  render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: ' Remy '}});
  fireEvent.change(screen.getByLabelText(/Description/), {target: {value: ' Blue jacket '}});
  const files = [photo(), photo('second.png')]; pick(files);
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  expect(uploadStudioImage).toHaveBeenCalledTimes(1);
  expect(createStudioSubject).not.toHaveBeenCalled();
  expect(screen.getByText('Uploading photo 1 of 2 to this workspace…')).toBeInTheDocument();
  await act(async () => finish({id:'first', name:'photo.png'}));
  await screen.findByText('Reference set saved to this workspace.');
  expect(uploadStudioImage).toHaveBeenNthCalledWith(1, files[0], 'project', expect.any(AbortSignal));
  expect(uploadStudioImage).toHaveBeenNthCalledWith(2, files[1], 'project', expect.any(AbortSignal));
  expect(createStudioSubject).toHaveBeenCalledExactlyOnceWith('project', 'Remy', 'Blue jacket', ['first','second'], expect.any(AbortSignal));
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1'); expect(revokeURL).toHaveBeenCalledWith('blob:photo-2');
});

it('rejects six files, unsupported types, and oversized files before upload', async () => {
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  pick(Array.from({length:6},(_, i) => photo(String(i)+'.png')));
  expect(screen.getByRole('alert')).toHaveTextContent('between 1 and 5');
  pick([new File(['svg'], 'image.svg', {type:'image/svg+xml'})]);
  expect(screen.getByRole('alert')).toHaveTextContent('static PNG, JPEG or WebP');
  const big = photo(); Object.defineProperty(big, 'size', {value: 8*1024*1024+1}); pick([big]);
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(createURL).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name:'Save reference set'})).toBeDisabled();
});

it('requires a two-step metadata-only removal and preserves cancel', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  fireEvent.click(screen.getByRole('button', {name:'Remove Remy'}));
  expect(deleteStudioSubject).not.toHaveBeenCalled();
  const confirm = screen.getByRole('group', {name:'Confirm removal of Remy'});
  expect(confirm).toHaveTextContent('Uploaded image files will be kept');
  fireEvent.click(within(confirm).getByRole('button', {name:'Cancel'}));
  expect(screen.queryByRole('group', {name:'Confirm removal of Remy'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'Remove Remy'}));
  fireEvent.click(screen.getByRole('button', {name:'Remove set'}));
  await screen.findByText('Reference set removed. Uploaded image files are kept.');
  expect(deleteStudioSubject).toHaveBeenCalledExactlyOnceWith('saved', expect.any(AbortSignal));
  expect(screen.queryByRole('article', {name:'Remy'})).not.toBeInTheDocument();
});

it('discards late reads and drafts immediately when the owner and project change', async () => {
  let finish!: (rows: StudioSubject[]) => void;
  vi.mocked(loadStudioSubjects).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioSubjects projectId="project" ownerId="first" visible onSelect={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name:'Saved subjects & products'}));
  fireEvent.change(screen.getByLabelText('Name'), {target:{value:'Private draft'}}); pick();
  const signal = vi.mocked(loadStudioSubjects).mock.calls[0][1];
  view.rerender(<StudioSubjects projectId="next" ownerId="second" visible onSelect={vi.fn()}/>);
  expect(signal?.aborted).toBe(true); expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
  await act(async () => finish([subject]));
  await open();
  expect(screen.queryByRole('article', {name:'Remy'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Name')).toHaveValue('');
  expect(loadStudioSubjects).toHaveBeenLastCalledWith('next', expect.any(AbortSignal));
});

it.each(['termx-locked','termx-session-locked','termx-signed-out'])('aborts pending uploads and discards their late receipts on %s', async event => {
  let finish!: (value: {id:string;name:string}) => void;
  vi.mocked(uploadStudioImage).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target:{value:'Private draft'}}); pick([photo(),photo('second.png')]);
  fireEvent.click(screen.getByRole('button', {name:'Save reference set'}));
  const signal = vi.mocked(uploadStudioImage).mock.calls[0][2];
  fireEvent(window, new Event(event));
  expect(signal?.aborted).toBe(true); expect(screen.queryByText('Private draft')).not.toBeInTheDocument();
  await act(async () => finish({id:'late', name:'late.png'}));
  expect(uploadStudioImage).toHaveBeenCalledTimes(1); expect(createStudioSubject).not.toHaveBeenCalled();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
});

it.each(['termx-locked', 'termx-session-locked', 'termx-signed-out'])('clears retained drafts and selected photos across %s and unlock', async event => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Private name'}});
  fireEvent.change(screen.getByLabelText(/Description/), {target: {value: 'Private notes'}});
  fireEvent.change(screen.getByLabelText('Find a saved set'), {target: {value: 'jacket'}});
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'})); pick();
  fireEvent(window, new Event(event));
  fireEvent(window, new Event('termx-session-unlocked'));
  expect(screen.getByRole('button', {name: 'Saved subjects & products'})).toHaveAttribute('aria-expanded', 'false');
  await open();
  expect(screen.getByLabelText('Name')).toHaveValue('');
  expect(screen.getByLabelText(/Description/)).toHaveValue('');
  expect(screen.getByLabelText('Find a saved set')).toHaveValue('');
  expect(screen.getByRole('button', {name: 'Photo 1 for Remy'})).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText('Add photos (0/5)')).toBeInTheDocument();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
});

it('hides and cancels requests while retaining the open panel when its parent is hidden', async () => {
  let finish!: (rows: StudioSubject[]) => void;
  vi.mocked(loadStudioSubjects).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name:'Saved subjects & products'}));
  const signal = vi.mocked(loadStudioSubjects).mock.calls[0][1];
  view.rerender(<StudioSubjects projectId="project" visible={false} onSelect={vi.fn()}/>);
  await act(async () => finish([subject]));
  expect(signal?.aborted).toBe(true); expect(screen.queryByRole('article')).not.toBeInTheDocument();
  view.rerender(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>);
  expect(screen.getByRole('button', {name:'Saved subjects & products'})).toHaveAttribute('aria-expanded','true');
});

it.each(['parent', 'document'])('preserves subject text, picked files and selected saved photos after %s hiding while refreshing previews', async change => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const select = vi.fn();
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={select}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Keep this draft'}});
  fireEvent.change(screen.getByLabelText(/Description/), {target: {value: 'Matte ceramic finish'}});
  fireEvent.change(screen.getByLabelText('Find a saved set'), {target: {value: 'jacket'}});
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'}));
  const original = photo(); pick([original]);
  expect(screen.getByRole('img', {name: 'photo.png'})).toHaveAttribute('src', 'blob:photo-1');
  if (change === 'parent') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={select}/>);
  else {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    fireEvent(document, new Event('visibilitychange'));
  }
  expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
  if (change === 'parent') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={select}/>);
  else {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
    fireEvent(document, new Event('visibilitychange'));
  }
  expect(screen.getByLabelText('Name')).toHaveValue('Keep this draft');
  expect(screen.getByLabelText(/Description/)).toHaveValue('Matte ceramic finish');
  expect(screen.getByLabelText('Find a saved set')).toHaveValue('jacket');
  expect(screen.getByText('Add photos (1/5)')).toBeInTheDocument();
  expect(screen.getByRole('img', {name: 'photo.png'})).toHaveAttribute('src', 'blob:photo-2');
  expect(createURL).toHaveBeenNthCalledWith(2, original);
  expect(revokeURL).not.toHaveBeenCalledWith('blob:photo-2');
  await screen.findByRole('button', {name: 'Photo 2 for Remy'});
  expect(screen.getByRole('button', {name: 'Photo 2 for Remy'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('portrait-b', 'Remy', 'Blue jacket');
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(createStudioSubject).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', {name: 'Save reference set'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  await screen.findByText('Reference set saved to this workspace.');
  expect(uploadStudioImage).toHaveBeenCalledExactlyOnceWith(original, 'project', expect.any(AbortSignal));
  expect(createStudioSubject).toHaveBeenCalledExactlyOnceWith('project', 'Keep this draft', 'Matte ceramic finish', ['uploaded'], expect.any(AbortSignal));
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-2');
});

it.each(['parent', 'document', 'observation'])('aborts uploads on %s hiding without losing text or accepting late receipts after returning', async change => {
  let finish!: (value: {id: string; name: string}) => void;
  vi.mocked(uploadStudioImage).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Keep interrupted draft'}});
  fireEvent.change(screen.getByLabelText(/Description/), {target: {value: 'Keep these notes'}});
  pick([photo(), photo('second.png')]);
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  const signal = vi.mocked(uploadStudioImage).mock.calls[0][2];
  if (change === 'observation') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible observing={false} onSelect={vi.fn()}/>);
  else if (change === 'parent') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={vi.fn()}/>);
  else {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    fireEvent(document, new Event('visibilitychange'));
  }
  expect(signal?.aborted).toBe(true);
  if (change === 'observation') expect(screen.getByLabelText('Name')).toHaveValue('Keep interrupted draft');
  if (change === 'observation' || change === 'parent') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>);
  else {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
    fireEvent(document, new Event('visibilitychange'));
  }
  await act(async () => finish({id: 'late', name: 'late.png'}));
  expect(screen.getByLabelText('Name')).toHaveValue('Keep interrupted draft');
  expect(screen.getByLabelText(/Description/)).toHaveValue('Keep these notes');
  expect(screen.getByText('Add photos (2/5)')).toBeInTheDocument();
  expect(screen.getByText(/The action was interrupted/)).toBeInTheDocument();
  expect(screen.queryByText('Reference set saved to this workspace.')).not.toBeInTheDocument();
  expect(screen.getByRole('img', {name: 'photo.png'})).toHaveAttribute('src', 'blob:photo-3');
  expect(screen.getByRole('img', {name: 'second.png'})).toHaveAttribute('src', 'blob:photo-4');
  expect(screen.getByRole('button', {name: 'Save reference set'})).toBeEnabled();
  expect(uploadStudioImage).toHaveBeenCalledTimes(1); expect(createStudioSubject).not.toHaveBeenCalled();
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1'); expect(revokeURL).toHaveBeenCalledWith('blob:photo-2');
});

it('recreates only live local previews across repeated hiding and keeps the original ordered files for saving', async () => {
  const original = [photo('front.png'), photo('side.png')];
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Two angles'}}); pick(original);
  for (let visit = 0; visit < 2; visit++) {
    view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={vi.fn()}/>);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>);
    const previews = screen.getAllByRole('img');
    expect(previews.map(image => image.getAttribute('src'))).toEqual(['blob:photo-' + (visit * 2 + 3), 'blob:photo-' + (visit * 2 + 4)]);
    previews.forEach(image => expect(revokeURL).not.toHaveBeenCalledWith(image.getAttribute('src')));
  }
  await waitFor(() => expect(screen.getByRole('button', {name: 'Save reference set'})).toBeEnabled());
  vi.mocked(uploadStudioImage).mockResolvedValueOnce({id: 'front', name: 'front.png'}).mockResolvedValueOnce({id: 'side', name: 'side.png'});
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  await screen.findByText('Reference set saved to this workspace.');
  expect(uploadStudioImage).toHaveBeenNthCalledWith(1, original[0], 'project', expect.any(AbortSignal));
  expect(uploadStudioImage).toHaveBeenNthCalledWith(2, original[1], 'project', expect.any(AbortSignal));
  expect(createStudioSubject).toHaveBeenCalledExactlyOnceWith('project', 'Two angles', '', ['front', 'side'], expect.any(AbortSignal));
  expect(revokeURL).toHaveBeenCalledTimes(6);
});

it.each(['owner', 'project'])('purges retained local files when the %s changes while hidden', async change => {
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Private draft'}}); pick();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={vi.fn()}/>);
  const projectId = change === 'project' ? 'next' : 'project', ownerId = change === 'owner' ? 'next' : 'owner';
  view.rerender(<StudioSubjects projectId={projectId} ownerId={ownerId} visible={false} onSelect={vi.fn()}/>);
  view.rerender(<StudioSubjects projectId={projectId} ownerId={ownerId} visible onSelect={vi.fn()}/>); await open();
  expect(screen.getByLabelText('Name')).toHaveValue('');
  expect(screen.getByText('Add photos (0/5)')).toBeInTheDocument();
  expect(screen.queryByRole('img', {name: 'photo.png'})).not.toBeInTheDocument();
  expect(createURL).toHaveBeenCalledTimes(1); expect(uploadStudioImage).not.toHaveBeenCalled();
});

it.each(['hide', 'observation'])('cancels a pending create on %s and ignores a late save receipt after returning', async change => {
  let finish!: (value: StudioSubject) => void;
  vi.mocked(createStudioSubject).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target: {value: 'Keep unsaved metadata'}}); pick();
  fireEvent.click(screen.getByRole('button', {name: 'Save reference set'}));
  await waitFor(() => expect(createStudioSubject).toHaveBeenCalledTimes(1));
  const signal = vi.mocked(createStudioSubject).mock.calls[0][4];
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={change !== 'hide'} observing={change !== 'observation'} onSelect={vi.fn()}/>);
  expect(signal?.aborted).toBe(true);
  if (change === 'observation') expect(screen.getByRole('group', {name: 'New reference set'})).toBeInTheDocument();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>);
  await act(async () => finish(subject));
  expect(screen.getByLabelText('Name')).toHaveValue('Keep unsaved metadata');
  expect(screen.queryByRole('article', {name: 'Remy'})).not.toBeInTheDocument();
  expect(screen.queryByText('Reference set saved to this workspace.')).not.toBeInTheDocument();
  expect(screen.getByText(/Review saved sets before retrying/)).toBeInTheDocument();
});

it.each(['hide', 'observation'])('cancels a pending removal on %s and requires fresh confirmation after returning', async change => {
  let finish!: () => void;
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  vi.mocked(deleteStudioSubject).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  fireEvent.click(screen.getByRole('button', {name: 'Remove Remy'}));
  fireEvent.click(screen.getByRole('button', {name: 'Remove set'}));
  const signal = vi.mocked(deleteStudioSubject).mock.calls[0][1];
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={change !== 'hide'} observing={change !== 'observation'} onSelect={vi.fn()}/>);
  expect(signal?.aborted).toBe(true);
  if (change === 'observation') expect(screen.getByRole('group', {name: 'New reference set'})).toBeInTheDocument();
  view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>);
  await act(async () => finish());
  expect(await screen.findByRole('article', {name: 'Remy'})).toBeInTheDocument();
  expect(screen.queryByRole('group', {name: 'Confirm removal of Remy'})).not.toBeInTheDocument();
  expect(screen.queryByText('Reference set removed. Uploaded image files are kept.')).not.toBeInTheDocument();
  expect(screen.getByText(/Review saved sets before retrying/)).toBeInTheDocument();
  expect(deleteStudioSubject).toHaveBeenCalledTimes(1);
});

it('revokes authenticated saved thumbnails when locked and blocks late thumbnail responses', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  let finish!: (value: Blob) => void;
  vi.mocked(transfer).mockResolvedValueOnce(new Blob(['image'], {type:'image/png'})).mockReturnValueOnce(new Promise(resolve => finish = resolve));
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  await waitFor(() => expect(screen.getByRole('img', {name:'Remy, photo 1'})).toHaveAttribute('src','blob:photo-1'));
  fireEvent(window, new Event('termx-session-locked'));
  await act(async () => finish(new Blob(['image'], {type:'image/png'})));
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1'); expect(createURL).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('keeps a failed upload draft recoverable without claiming it was saved', async () => {
  vi.mocked(uploadStudioImage).mockRejectedValue(Error('Upload interrupted'));
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  fireEvent.change(screen.getByLabelText('Name'), {target:{value:'Keep this name'}}); pick();
  fireEvent.click(screen.getByRole('button', {name:'Save reference set'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Upload interrupted');
  expect(screen.getByLabelText('Name')).toHaveValue('Keep this name');
  expect(screen.getByRole('button', {name:'Save reference set'})).toBeEnabled();
  expect(createStudioSubject).not.toHaveBeenCalled();
});

it('does not introduce a nested form or submit button in the studio composer', async () => {
  const view = render(<form><StudioSubjects projectId="project" visible onSelect={vi.fn()}/></form>); await open();
  expect(view.container.querySelectorAll('form')).toHaveLength(1);
  expect([...view.container.querySelectorAll('button')].every(button => button.type === 'button')).toBe(true);
});


it('offers the complete reference set only when a multi-image callback is provided and preserves saved order', async () => {
  const saved = {...subject, image_ids: ['third-angle', 'front-angle', 'side-angle']};
  vi.mocked(loadStudioSubjects).mockResolvedValue([saved]);
  const select = vi.fn(), selectMany = vi.fn();
  render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={select} onSelectMany={selectMany} maxReferences={3}/>);
  await open();
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Remy'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use reference set from Remy'}));
  expect(selectMany).toHaveBeenCalledExactlyOnceWith(['third-angle', 'front-angle', 'side-angle'], 'Remy', 'Blue jacket');
  expect(selectMany.mock.calls[0][0]).not.toBe(saved.image_ids);
  expect(select).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Saved subjects & products'})).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText('Selected all 3 reference photos from Remy in saved order.')).toBeInTheDocument();
  expect(screen.getByText(/References guide the output/)).toBeInTheDocument();
  expect(uploadStudioImage).not.toHaveBeenCalled(); expect(createStudioSubject).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('front-angle', 'Remy', 'Blue jacket');
});

it('keeps single-photo selection available when a saved set exceeds the model limit without truncation', async () => {
  const saved = {...subject, image_ids: ['one', 'two', 'three']};
  vi.mocked(loadStudioSubjects).mockResolvedValue([saved]);
  const select = vi.fn(), selectMany = vi.fn();
  render(<StudioSubjects projectId="project" visible onSelect={select} onSelectMany={selectMany} maxReferences={2}/>);
  await open();
  const set = screen.getByRole('button', {name: 'Use reference set from Remy'});
  expect(set).toBeDisabled();
  expect(set).toHaveAccessibleDescription('This set has 3 photos. This model supports up to 2 reference photos in this workspace. Choose one photo instead.');
  fireEvent.click(set); expect(selectMany).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Photo 3 for Remy'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Remy'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('three', 'Remy', 'Blue jacket');
  expect(saved.image_ids).toEqual(['one', 'two', 'three']);
});

it('preserves the single-photo-only interface without a multi-image callback', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()} maxReferences={5}/>); await open();
  expect(screen.queryByRole('button', {name: /Use reference set/})).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Use selected photo from Remy'})).toBeEnabled();
});

it.each([undefined, 0, -1, 1.5, Number.NaN])('fails closed for an absent or invalid multi-image limit %s', async max => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const selectMany = vi.fn();
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()} onSelectMany={selectMany} maxReferences={max}/>); await open();
  const set = screen.getByRole('button', {name: 'Use reference set from Remy'});
  expect(set).toBeDisabled(); fireEvent.click(set); expect(selectMany).not.toHaveBeenCalled();
});

it.each(['hidden', 'termx-locked', 'termx-session-locked', 'termx-signed-out', 'document-hidden'])('prevents set selection after %s', async change => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const selectMany = vi.fn(), select = vi.fn();
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={select} onSelectMany={selectMany} maxReferences={5}/>); await open();
  const staleSet = screen.getByRole('button', {name: 'Use reference set from Remy'});
  const stalePhoto = screen.getByRole('button', {name: 'Use selected photo from Remy'});
  if (change === 'hidden') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={select} onSelectMany={selectMany} maxReferences={5}/>);
  else if (change === 'document-hidden') {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    fireEvent(document, new Event('visibilitychange'));
  } else fireEvent(window, new Event(change));
  fireEvent.click(staleSet); fireEvent.click(stalePhoto);
  expect(selectMany).not.toHaveBeenCalled(); expect(select).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', {name: /Use reference set/})).not.toBeInTheDocument();
});

it('uses the current model limit after a change and never silently shrinks a set', async () => {
  vi.mocked(loadStudioSubjects).mockResolvedValue([subject]);
  const selectMany = vi.fn();
  const view = render(<StudioSubjects projectId="project" visible onSelect={vi.fn()} onSelectMany={selectMany} maxReferences={5}/>); await open();
  expect(screen.getByRole('button', {name: 'Use reference set from Remy'})).toBeEnabled();
  view.rerender(<StudioSubjects projectId="project" visible onSelect={vi.fn()} onSelectMany={selectMany} maxReferences={1}/>);
  const set = screen.getByRole('button', {name: 'Use reference set from Remy'});
  expect(set).toBeDisabled(); fireEvent.click(set); expect(selectMany).not.toHaveBeenCalled();
  expect(screen.getByText(/This set has 2 photos/)).toHaveTextContent('supports up to 1 reference photo');
  expect(subject.image_ids).toEqual(['portrait-a', 'portrait-b']);
});


function observeSubjectPhotos() {
  const watched = new Map<Element, IntersectionObserverCallback>();
  vi.stubGlobal('IntersectionObserver', class {
    callback: IntersectionObserverCallback;
    elements = new Set<Element>();
    constructor(callback: IntersectionObserverCallback) {this.callback = callback;}
    observe(element: Element) {this.elements.add(element); watched.set(element, this.callback);}
    disconnect() {this.elements.forEach(element => watched.delete(element)); this.elements.clear();}
  });
  return {
    watched,
    show(button: HTMLElement, visible: boolean) {
      const element = button.querySelector('.subject-photo-preview')!;
      watched.get(element)?.([{target: element, isIntersecting: visible, intersectionRatio: visible ? 1 : 0} as IntersectionObserverEntry], {} as IntersectionObserver);
    },
  };
}
function savedSets(count: number, photos = 5): StudioSubject[] {
  return Array.from({length: count}, (_, index) => ({...subject, id: 'saved-' + index, name: 'Subject ' + index,
    image_ids: Array.from({length: photos}, (_, photo) => 'portrait-' + index + '-' + photo)}));
}

it('keeps 500 offscreen saved photos as metadata without binary transfers and retains both selection actions', async () => {
  const observer = observeSubjectPhotos();
  vi.mocked(loadStudioSubjects).mockResolvedValue(savedSets(100));
  const select = vi.fn(), selectMany = vi.fn();
  render(<StudioSubjects projectId="project" visible onSelect={select} onSelectMany={selectMany} maxReferences={5}/>);
  await open();
  expect(observer.watched.size).toBe(500);
  expect(transfer).not.toHaveBeenCalled(); expect(createURL).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Photo 4 for Subject 99'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Subject 99'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use reference set from Subject 99'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('portrait-99-3', 'Subject 99', 'Blue jacket');
  expect(selectMany).toHaveBeenCalledExactlyOnceWith(['portrait-99-0','portrait-99-1','portrait-99-2','portrait-99-3','portrait-99-4'], 'Subject 99', 'Blue jacket');
  expect(transfer).not.toHaveBeenCalled();
});

it('bounds visible saved-photo transfers to three, keeping aborted native transfers in their slots until they settle', async () => {
  const observer = observeSubjectPhotos();
  vi.mocked(loadStudioSubjects).mockResolvedValue(savedSets(2, 3));
  const pending: Array<{resolve: (blob: Blob) => void; signal: AbortSignal}> = [];
  let active = 0, peak = 0;
  vi.mocked(transfer).mockImplementation((_, init) => new Promise(resolve => {
    active++; peak = Math.max(peak, active);
    pending.push({signal: init!.signal as AbortSignal, resolve: blob => {active--; resolve(blob);}});
  }));
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  const photos = screen.getAllByRole('button', {name: /^Photo [123] for Subject/});
  act(() => photos.forEach(button => observer.show(button, true)));
  expect(transfer).toHaveBeenCalledTimes(3); expect(peak).toBe(3);
  act(() => observer.show(photos[0], false));
  expect(pending[0].signal.aborted).toBe(true);
  expect(transfer).toHaveBeenCalledTimes(3);
  await act(async () => pending[0].resolve(new Blob(['photo'], {type:'image/png'})));
  expect(transfer).toHaveBeenCalledTimes(4); expect(peak).toBe(3);
  for (let index = 1; index < 6; index++) {
    await act(async () => pending[index].resolve(new Blob(['photo'], {type:'image/png'})));
  }
  expect(transfer).toHaveBeenCalledTimes(6); expect(peak).toBe(3); expect(active).toBe(0);
  expect(createURL).toHaveBeenCalledTimes(5);
});

it('aborts queued offscreen photos before they start and revokes completed previews when a photo leaves view', async () => {
  const observer = observeSubjectPhotos();
  vi.mocked(loadStudioSubjects).mockResolvedValue(savedSets(1, 5));
  const pending: Array<{resolve: (blob: Blob) => void; signal: AbortSignal}> = [];
  vi.mocked(transfer).mockImplementation((_, init) => new Promise(resolve => pending.push({resolve, signal: init!.signal as AbortSignal})));
  render(<StudioSubjects projectId="project" visible onSelect={vi.fn()}/>); await open();
  const photos = screen.getAllByRole('button', {name: /^Photo [1-5] for Subject/});
  act(() => photos.forEach(button => observer.show(button, true)));
  expect(transfer).toHaveBeenCalledTimes(3);
  act(() => {observer.show(photos[3], false); observer.show(photos[4], false);});
  await act(async () => pending[0].resolve(new Blob(['photo'], {type:'image/png'})));
  expect(transfer).toHaveBeenCalledTimes(3);
  expect(within(photos[0]).getByRole('img')).toHaveAttribute('src', 'blob:photo-1');
  act(() => observer.show(photos[0], false));
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-1');
  expect(within(photos[0]).getByRole('img')).not.toHaveAttribute('src');
  act(() => {observer.show(photos[1], false); observer.show(photos[2], false);});
  expect(pending[1].signal.aborted).toBe(true); expect(pending[2].signal.aborted).toBe(true);
  await act(async () => {pending[1].resolve(new Blob(['late'], {type:'image/png'})); pending[2].resolve(new Blob(['late'], {type:'image/png'}));});
  expect(createURL).toHaveBeenCalledTimes(1); expect(transfer).toHaveBeenCalledTimes(3);
  act(() => observer.show(photos[0], true));
  expect(transfer).toHaveBeenCalledTimes(4);
  expect(within(photos[0]).getByRole('img')).not.toHaveAttribute('src');
  await act(async () => pending[3].resolve(new Blob(['new'], {type:'image/png'})));
  expect(within(photos[0]).getByRole('img')).toHaveAttribute('src', 'blob:photo-2');
  fireEvent.click(screen.getByRole('button', {name: 'Saved subjects & products'}));
  expect(revokeURL).toHaveBeenCalledWith('blob:photo-2'); expect(observer.watched.size).toBe(0);
});

it.each(['close', 'hidden', 'owner', 'termx-session-locked', 'document-hidden', 'observation'])('discards running and queued saved previews on %s', async change => {
  const observer = observeSubjectPhotos();
  vi.mocked(loadStudioSubjects).mockResolvedValue(savedSets(2, 3));
  const pending: Array<{resolve: (blob: Blob) => void; signal: AbortSignal}> = [];
  vi.mocked(transfer).mockImplementation((_, init) => new Promise(resolve => pending.push({resolve, signal: init!.signal as AbortSignal})));
  const view = render(<StudioSubjects projectId="project" ownerId="owner" visible onSelect={vi.fn()}/>); await open();
  const photos = screen.getAllByRole('button', {name: /^Photo [123] for Subject/});
  act(() => photos.forEach(button => observer.show(button, true)));
  expect(transfer).toHaveBeenCalledTimes(3);
  if (change === 'observation') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible observing={false} onSelect={vi.fn()}/>);
  else if (change === 'close') fireEvent.click(screen.getByRole('button', {name: 'Saved subjects & products'}));
  else if (change === 'hidden') view.rerender(<StudioSubjects projectId="project" ownerId="owner" visible={false} onSelect={vi.fn()}/>);
  else if (change === 'owner') view.rerender(<StudioSubjects projectId="next" ownerId="next-owner" visible onSelect={vi.fn()}/>);
  else if (change === 'document-hidden') {
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    fireEvent(document, new Event('visibilitychange'));
  } else fireEvent(window, new Event(change));
  expect(pending.every(item => item.signal.aborted)).toBe(true);
  if (change === 'observation') expect(screen.getByRole('group', {name: 'New reference set'})).toBeInTheDocument();
  await act(async () => pending.forEach(item => item.resolve(new Blob(['late'], {type:'image/png'}))));
  expect(transfer).toHaveBeenCalledTimes(3); expect(createURL).not.toHaveBeenCalled(); expect(observer.watched.size).toBe(0);
});

it('limits the no-observer fallback to three photos across all saved sets', async () => {
  vi.stubGlobal('IntersectionObserver', undefined);
  vi.mocked(loadStudioSubjects).mockResolvedValue(savedSets(30, 2));
  vi.mocked(transfer).mockResolvedValue(new Blob(['photo'], {type:'image/png'}));
  const select = vi.fn();
  render(<StudioSubjects projectId="project" visible onSelect={select}/>); await open();
  await waitFor(() => expect(createURL).toHaveBeenCalledTimes(3));
  expect(transfer).toHaveBeenCalledTimes(3);
  expect(vi.mocked(transfer).mock.calls.map(call => call[0])).toEqual([
    '/api/media/artifacts/portrait-0-0/export?format=original',
    '/api/media/artifacts/portrait-0-1/export?format=original',
    '/api/media/artifacts/portrait-1-0/export?format=original',
  ]);
  fireEvent.click(screen.getByRole('button', {name: 'Photo 2 for Subject 29'}));
  fireEvent.click(screen.getByRole('button', {name: 'Use selected photo from Subject 29'}));
  expect(select).toHaveBeenCalledExactlyOnceWith('portrait-29-1', 'Subject 29', 'Blue jacket');
  expect(transfer).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole('button', {name: 'Saved subjects & products'}));
  expect(revokeURL).toHaveBeenCalledTimes(3);
});
