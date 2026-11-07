import {request, json} from './api';

export const characterStates = ['idle', 'thinking', 'working', 'waiting', 'success', 'error', 'paused', 'sleeping'] as const;
export type CharacterState = (typeof characterStates)[number];
export const characterClips = [...characterStates, 'walk', 'hop', 'fall', 'sit', 'drag', 'wave'] as const;
export type CharacterClipName = (typeof characterClips)[number];

export type CharacterFrame = {atlas: string | number; x: number; y: number; width: number; height: number; duration_ms: number};
export type CharacterClip = {frames: CharacterFrame[]; loop: boolean};
export type CharacterManifest = {
  version: number;
  id: string;
  name: string;
  revision: number;
  logical_size: {width: number; height: number};
  foot_anchor: {x: number; y: number};
  interaction_bounds: {x: number; y: number; width: number; height: number};
  atlases: {file: string; width: number; height: number}[];
  clips: Record<string, CharacterClip>;
  states: Record<string, string>;
};
export type CharacterPack = {
  id: string;
  name: string;
  revision: number;
  builtin: boolean;
  accepted: boolean;
  accepted_revision?: number | null;
  reference_id?: string;
  fallback_clips?: string[];
};
export type CompanionPreferences = {
  enabled: boolean;
  size: number;
  activity: 'calm' | 'playful';
  reduced_motion: boolean;
  pinned: string[];
  hidden: string[];
  assignments: Record<string, string>;
};
export type OperationStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown';
export type OperationPhase = 'reference' | 'clip' | 'reference_ready' | 'preview_ready';
export type CharacterOperation = {
  id: string;
  status: OperationStatus;
  phase: OperationPhase;
  pack_id?: string;
  artifact_id?: string;
  error?: string;
  created_at?: number;
};
export type CharacterLibrary = {packs: CharacterPack[]; preferences: CompanionPreferences; operations: CharacterOperation[]};
export type CompanionPet = {
  id: string;
  session_id: string;
  task_id: string | null;
  name: string;
  state: CharacterState;
  pack_id: string;
  pack_revision: number;
  roaming: boolean;
  action?: {id: string; name: 'wave' | 'sit' | 'hop' | 'sleep' | 'home' | 'approach'; target?: string; expires_at: number} | null;
};
export type CompanionRuntime = {enabled: boolean; preferences: CompanionPreferences; pets: CompanionPet[]};

export const characterPath = (id: string) => '/api/characters/packs/' + encodeURIComponent(id);

export function characterDimensions(logicalSize: {width: number; height: number}, size: number) {
  const scale = size / Math.max(logicalSize.width, logicalSize.height);
  return {width: logicalSize.width * scale, height: logicalSize.height * scale, scale};
}

export function clipFor(manifest: CharacterManifest, state: string) {
  const name = manifest.states[state] || state;
  return manifest.clips[name] || manifest.clips.idle || Object.values(manifest.clips)[0];
}

export function frameAt(clip: CharacterClip | undefined, elapsed: number) {
  if (!clip?.frames.length) return 0;
  const duration = clip.frames.reduce((sum, frame) => sum + frame.duration_ms, 0);
  let offset = clip.loop ? Math.max(0, elapsed) % duration : Math.min(Math.max(0, elapsed), duration - 1);
  for (let index = 0; index < clip.frames.length; index++) {
    offset -= clip.frames[index].duration_ms;
    if (offset < 0) return index;
  }
  return clip.frames.length - 1;
}

/** Milliseconds until the frame after the one showing at `elapsed`, or null when the clip holds its last frame for good. */
export function frameDelay(clip: CharacterClip | undefined, elapsed: number): number | null {
  if (!clip || clip.frames.length < 2) return null;
  const duration = clip.frames.reduce((sum, frame) => sum + frame.duration_ms, 0);
  if (!clip.loop && elapsed >= duration - 1) return null;
  let offset = clip.loop ? Math.max(0, elapsed) % duration : Math.max(0, elapsed);
  for (const frame of clip.frames) {
    if (offset < frame.duration_ms) return frame.duration_ms - offset;
    offset -= frame.duration_ms;
  }
  return null;
}

export function habitatPosition(start: number, direction: number, elapsed: number, width: number, size: number, speed: number) {
  const range = Math.max(0, width - size);
  if (!range) return {x: 0, direction: 1};
  const distance = start + (direction * Math.max(0, elapsed) * speed) / 1000;
  const period = 2 * range;
  const wrapped = ((distance % period) + period) % period;
  return {x: wrapped <= range ? wrapped : period - wrapped, direction: (wrapped < range ? 1 : -1) * direction};
}

export function companionClip(pet: CompanionPet, walking: boolean, now = Date.now() / 1000) {
  if (['waiting', 'error', 'success', 'paused'].includes(pet.state)) return pet.state;
  if (pet.action && pet.action.expires_at > now) {
    if (pet.action.name === 'sleep') return 'sleeping';
    if (['wave', 'sit', 'hop'].includes(pet.action.name)) return pet.action.name;
  }
  return walking ? 'walk' : pet.state;
}

export async function updateCompanions(changes: Partial<CompanionPreferences>) {
  const value = await request<CompanionPreferences>('/api/companions/preferences', json('PATCH', changes));
  window.dispatchEvent(new Event('termx-companions-changed'));
  return value;
}

/** Returns `previous` when `next` is structurally equal, so React skips the re-render. */
export function keepIfSame<T>(previous: T, next: T): T {
  try {
    return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
  } catch {
    return next;
  }
}

/** Operations that are still running on the host. Failed and unknown ones stay listed but need no polling. */
export const isRunning = (operation: CharacterOperation) => operation.status === 'queued' || operation.status === 'running';

/** The text to show for a caught value. */
export const errorMessage = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

export interface AssignmentRow {id: string; title: string}

/** One row per live conversation, then pets whose conversation is gone, then hidden ids that match nothing. */
export function assignmentRows(sessions: {id: string; title: string; archived?: boolean}[], pets: CompanionPet[], preferences: CompanionPreferences): AssignmentRow[] {
  const rows: AssignmentRow[] = sessions.filter(row => !row.archived).map(row => ({id: row.id, title: row.title}));
  for (const pet of pets) {
    if (sessions.some(row => row.id === pet.id) || rows.some(row => row.id === pet.id)) continue;
    rows.push({id: pet.id, title: pet.name + ' · ' + (sessions.find(row => row.id === pet.session_id)?.title || 'Agent')});
  }
  for (const id of preferences.hidden) {
    if (!rows.some(row => row.id === id)) rows.push({id, title: 'Hidden agent ' + id.slice(0, 8)});
  }
  return rows;
}

/** Parses a decimal field. Empty, non-numeric, out-of-range (and, with `integer`, fractional) text gives null. */
export function parseNumberField(text: string, min: number, max: number, integer = false): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max) return null;
  if (integer && !Number.isInteger(value)) return null;
  return value;
}

// Server replies are untrusted input: check the fields the UI reads before any of it reaches state.
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const isStrings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const reject = (what: string): never => {
  throw new Error('The host sent an unexpected ' + what + '. Refresh and try again.');
};

function isPreferences(value: unknown): value is CompanionPreferences {
  return isRecord(value)
    && typeof value.enabled === 'boolean'
    && typeof value.size === 'number'
    && (value.activity === 'calm' || value.activity === 'playful')
    && typeof value.reduced_motion === 'boolean'
    && isStrings(value.pinned)
    && isStrings(value.hidden)
    && isRecord(value.assignments)
    && Object.values(value.assignments).every(item => typeof item === 'string');
}

function isPack(value: unknown): value is CharacterPack {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.revision === 'number'
    && typeof value.builtin === 'boolean'
    && typeof value.accepted === 'boolean';
}

const operationStatuses: readonly unknown[] = ['queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'];
const operationPhases: readonly unknown[] = ['reference', 'clip', 'reference_ready', 'preview_ready'];
function isOperation(value: unknown): value is CharacterOperation {
  return isRecord(value) && typeof value.id === 'string' && operationStatuses.includes(value.status) && operationPhases.includes(value.phase);
}

export function parseCompanionPreferences(value: unknown): CompanionPreferences {
  return isPreferences(value) ? value : reject('preferences reply');
}

export function parseCharacterLibrary(value: unknown): CharacterLibrary {
  if (!isRecord(value) || !Array.isArray(value.packs) || !value.packs.every(isPack)) return reject('character library');
  const operations = value.operations === undefined ? [] : value.operations;
  if (!Array.isArray(operations) || !operations.every(isOperation)) return reject('character library');
  return {packs: value.packs, preferences: parseCompanionPreferences(value.preferences), operations};
}

export function parseCompanionPets(value: unknown): CompanionPet[] {
  if (!isRecord(value)) return reject('companion list');
  return Array.isArray(value.pets) ? (value.pets as CompanionPet[]) : [];
}

export function asCharacterPack(value: unknown): CharacterPack {
  return isPack(value) ? value : reject('character pack reply');
}

function parseJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return reject(what);
  }
}

export function parseCharacterPack(text: string): CharacterPack {
  return asCharacterPack(parseJson(text, 'character pack reply'));
}

export function parseArtifactId(text: string): string {
  const value = parseJson(text, 'upload reply');
  return isRecord(value) && typeof value.id === 'string' && value.id ? value.id : reject('upload reply');
}
