import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {request} from '../../lib/api';
import {expectNoAxeViolations} from '../../test-axe';
import {DescribeToDraft, type DescribeToDraftProps} from './DescribeToDraft';

vi.mock('../../lib/api', () => ({request: vi.fn(), json: (method: string, value: unknown) => ({method, body: JSON.stringify(value)})}));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => { mocked.mockReset(); });

const agentDraft = {name: 'Security reviewer', description: 'Reviews diffs.', instructions: 'Read the diff and list risks.', tools: ['read_file', 'git_diff'], approval_mode: 'standard', sandbox_profile: 'agent'};
const result = (extra: Record<string, unknown>) => ({data: {draft_definition: {status: 'drafted', reply: 'Drafted it.', draft: agentDraft, warnings: [], provider_id: 'p', model: 'fixture-model', ...extra}}});
const variables = (call = 0) => JSON.parse(String(mocked.mock.calls[call][1]!.body)).variables.i;

function open(props: Partial<DescribeToDraftProps> = {}) {
  const handlers = {onClose: vi.fn(), onReview: vi.fn(), onManual: vi.fn()};
  const view = render(<DescribeToDraft kind="agent" open {...handlers} {...props} />);
  return {...view, ...handlers};
}
const type = (text: string, label = /Describe the agent|Ask for a change/) => fireEvent.change(screen.getByLabelText(label), {target: {value: text}});

it('starts empty with one hint, focuses the composer and offers manual entry as the only other action', async () => {
  open();
  const dialog = screen.getByRole('dialog', {name: 'New agent'});
  expect(within(dialog).getByText(/For example: An agent that reads a pull request/)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText('Describe the agent')).toHaveFocus());
  expect(within(dialog).getByRole('button', {name: 'Fill in manually'})).toBeInTheDocument();
  expect(within(dialog).queryByRole('button', {name: 'Review and create'})).not.toBeInTheDocument();
  expect(within(dialog).getByRole('button', {name: 'Send description'})).toBeDisabled();
  expect(mocked).not.toHaveBeenCalled();
});

it('sends the description, shows the reply as prose and a live draft summary, and passes axe', async () => {
  mocked.mockResolvedValue(result({warnings: ['Left off tools beyond read-only: run_shell.']}));
  const {container} = open();
  type('Review pull requests for security problems');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  const draft = await screen.findByRole('region', {name: 'Draft'});
  expect(variables()).toEqual({kind: 'agent', messages: [{role: 'user', text: 'Review pull requests for security problems'}], current_draft: null, project_id: null});
  expect(within(draft).getByText('Security reviewer')).toBeInTheDocument();
  expect(within(draft).getByText('Read files, See git changes')).toBeInTheDocument();
  expect(within(draft).getByText('Asks before risky actions. Agent sandbox.')).toBeInTheDocument();
  expect(within(draft).getByText(/Left off tools beyond read-only: run_shell/)).toBeInTheDocument();
  expect(within(draft).getByText('Written by fixture-model')).toBeInTheDocument();
  expect(screen.getByRole('log', {name: 'Conversation'})).toHaveTextContent('Drafted it.');
  await expectNoAxeViolations(container.ownerDocument.body);
});

it('refines with a follow-up, sending the earlier draft and the whole conversation', async () => {
  mocked.mockResolvedValueOnce(result({})).mockResolvedValueOnce(result({reply: 'Made it stricter.', draft: {...agentDraft, name: 'Strict reviewer'}}));
  open();
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  await screen.findByRole('region', {name: 'Draft'});
  type('Make it stricter', /Ask for a change/);
  fireEvent.click(screen.getByRole('button', {name: 'Send change'}));
  expect(await screen.findByText('Strict reviewer')).toBeInTheDocument();
  expect(variables(1).current_draft).toEqual(agentDraft);
  expect(variables(1).messages).toEqual([{role: 'user', text: 'Review code'}, {role: 'assistant', text: 'Drafted it.'}, {role: 'user', text: 'Make it stricter'}]);
});

it('hands the draft to the caller on Review and create, and the caller closes nothing it did not ask for', async () => {
  mocked.mockResolvedValue(result({}));
  const {onReview, onManual} = open();
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  fireEvent.click(await screen.findByRole('button', {name: 'Review and create'}));
  expect(onReview).toHaveBeenCalledWith(agentDraft);
  expect(onManual).not.toHaveBeenCalled();
});

it('submits with Enter, keeps Shift+Enter for a new line, and ignores a second send while drafting', async () => {
  let release!: (value: unknown) => void;
  mocked.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  open();
  const box = screen.getByLabelText('Describe the agent');
  fireEvent.change(box, {target: {value: 'Review code'}});
  fireEvent.keyDown(box, {key: 'Enter', shiftKey: true});
  expect(mocked).not.toHaveBeenCalled();
  fireEvent.submit(box.closest('form')!);
  fireEvent.submit(box.closest('form')!);
  expect(mocked).toHaveBeenCalledTimes(1);
  expect(await screen.findByRole('status')).toHaveTextContent('Drafting…');
  expect(screen.getByRole('button', {name: 'Send description'})).toBeDisabled();
  release(result({}));
  await screen.findByRole('region', {name: 'Draft'});
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('shows a failure inline with Try again, keeping what was typed and any earlier draft', async () => {
  mocked.mockRejectedValueOnce(new Error('The model took too long to answer.')).mockResolvedValueOnce(result({}));
  open();
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('The model took too long to answer.');
  expect(screen.getByRole('log')).toHaveTextContent('Review code');
  fireEvent.click(within(alert).getByRole('button', {name: 'Try again'}));
  await screen.findByRole('region', {name: 'Draft'});
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(variables(1).messages).toEqual([{role: 'user', text: 'Review code'}]);
});

it('turns a missing model account into a setup action', async () => {
  mocked.mockResolvedValue(result({status: 'no_provider', reply: '', draft: null}));
  const onSetupProvider = vi.fn();
  const {onClose} = open({onSetupProvider});
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('No model account is set up');
  fireEvent.click(within(alert).getByRole('button', {name: 'Set up a model provider'}));
  expect(onSetupProvider).toHaveBeenCalled();
  expect(onClose).toHaveBeenCalled();
});

it('says where to add an account when the caller cannot navigate', async () => {
  mocked.mockResolvedValue(result({status: 'no_provider', reply: '', draft: null}));
  open();
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Add one in Settings, under Models and engines.');
});

it('shows a question from the model without replacing the draft', async () => {
  mocked.mockResolvedValue(result({status: 'needs_input', reply: 'Which repository should it watch?', draft: null}));
  open();
  type('Watch things');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  expect(await screen.findByText('Which repository should it watch?')).toBeInTheDocument();
  expect(screen.queryByRole('region', {name: 'Draft'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Review and create'})).not.toBeInTheDocument();
});

it('starts over without a draft and closes on Escape', async () => {
  mocked.mockResolvedValue(result({}));
  const {onClose} = open();
  type('Review code');
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  await screen.findByRole('region', {name: 'Draft'});
  fireEvent.click(screen.getByRole('button', {name: 'Start over'}));
  expect(screen.queryByRole('region', {name: 'Draft'})).not.toBeInTheDocument();
  expect(screen.getByText(/For example:/)).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole('dialog'), {key: 'Escape'});
  expect(onClose).toHaveBeenCalled();
});

it('offers Fill in manually at any point', async () => {
  const {onManual} = open();
  fireEvent.click(screen.getByRole('button', {name: 'Fill in manually'}));
  expect(onManual).toHaveBeenCalledOnce();
});

it('passes the project for a skill and shows the SKILL.md in a named region', async () => {
  mocked.mockResolvedValue(result({draft: {name: 'release-notes', description: 'Use for release notes.', markdown: '---\nname: "release-notes"\n---\n\nBody\n'}}));
  open({kind: 'skill', projectId: 'p1'});
  expect(screen.getByRole('dialog', {name: 'New skill'})).toBeInTheDocument();
  type('Write release notes', /Describe the skill/);
  fireEvent.click(screen.getByRole('button', {name: 'Send description'}));
  expect(await screen.findByRole('region', {name: 'SKILL.md'})).toHaveTextContent('release-notes');
  expect(variables().project_id).toBe('p1');
  expect(variables().kind).toBe('skill');
});

it('limits a long conversation and a long message before sending', async () => {
  open();
  expect(screen.getByLabelText('Describe the agent')).toHaveAttribute('maxlength', '4000');
});
