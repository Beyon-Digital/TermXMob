import {describe, it, expect} from 'vitest';
import {
  assignmentRows,
  asCharacterPack,
  characterDimensions,
  companionClip,
  frameAt,
  frameDelay,
  habitatPosition,
  keepIfSame,
  parseArtifactId,
  parseCharacterLibrary,
  parseCharacterPack,
  parseNumberField,
  type CharacterClip,
  type CompanionPet,
  type CompanionPreferences,
} from './characters';

const clip: CharacterClip = {loop: true, frames: [70, 120, 90].map(duration_ms => ({atlas: 0, x: 0, y: 0, width: 96, height: 112, duration_ms}))};
const pet: CompanionPet = {id: 'chat', session_id: 'chat', task_id: 'task', name: 'Sprout', state: 'idle', pack_id: 'termx-robot', pack_revision: 1, roaming: true};
const preferences: CompanionPreferences = {enabled: true, size: 96, activity: 'calm', reduced_motion: false, pinned: [], hidden: [], assignments: {}};

describe('character playback', () => {
  it('bounds tall and wide imported canvases by their longest edge', () => {
    expect(characterDimensions({width: 16, height: 512}, 96)).toEqual({width: 3, height: 96, scale: 0.1875});
    expect(characterDimensions({width: 512, height: 16}, 96)).toEqual({width: 96, height: 3, scale: 0.1875});
  });

  it('uses individual frame durations and loops exactly at the duration boundary', () => {
    expect([0, 69, 70, 189, 190, 279, 280, 350].map(time => frameAt(clip, time))).toEqual([0, 0, 1, 1, 2, 2, 0, 1]);
  });

  it('holds the last frame for a nonlooping celebration', () => {
    expect(frameAt({...clip, loop: false}, 2800)).toBe(2);
    expect(frameAt(clip, -10)).toBe(0);
    expect(frameAt(undefined, 20)).toBe(0);
  });

  it('says how long until the next frame, and never for a still or finished clip', () => {
    expect([0, 69, 70, 189, 190, 279, 280].map(time => frameDelay(clip, time))).toEqual([70, 1, 120, 1, 90, 1, 70]);
    expect(frameDelay({...clip, loop: false}, 100)).toBe(90);
    expect(frameDelay({...clip, loop: false}, 280)).toBeNull();
    expect(frameDelay({loop: true, frames: [clip.frames[0]]}, 10)).toBeNull();
    expect(frameDelay(undefined, 0)).toBeNull();
  });

  it('bounces within a habitat after crossing either edge', () => {
    expect(habitatPosition(190, 1, 1000, 300, 100, 30)).toEqual({x: 180, direction: -1});
    expect(habitatPosition(10, -1, 1000, 300, 100, 30)).toEqual({x: 20, direction: 1});
    expect(habitatPosition(10, -1, 1000, 80, 100, 30)).toEqual({x: 0, direction: 1});
  });

  it('gives task attention priority over agent emotes', () => {
    expect(companionClip({...pet, state: 'waiting', action: {id: 'one', name: 'wave', expires_at: 200}}, true, 100)).toBe('waiting');
    expect(companionClip({...pet, state: 'error', action: {id: 'one', name: 'sleep', expires_at: 200}}, false, 100)).toBe('error');
  });

  it('honors bounded emotes and discards expired ones', () => {
    expect(companionClip({...pet, action: {id: 'one', name: 'wave', expires_at: 200}}, true, 100)).toBe('wave');
    expect(companionClip({...pet, action: {id: 'one', name: 'wave', expires_at: 100}}, true, 100)).toBe('walk');
    expect(companionClip({...pet, action: {id: 'one', name: 'sleep', expires_at: 200}}, false, 100)).toBe('sleeping');
  });
});

describe('numeric fields', () => {
  it('rejects empty, blank, non-numeric, fractional and out-of-range text instead of reading it as zero', () => {
    expect([parseNumberField('', 20, 5000, true), parseNumberField('  ', 20, 5000, true), parseNumberField('abc', 1, 16), parseNumberField('2.5', 1, 16, true)]).toEqual([null, null, null, null]);
    expect([parseNumberField('19', 20, 5000, true), parseNumberField('5001', 20, 5000, true)]).toEqual([null, null]);
  });

  it('accepts values inside the range, including the bounds and decimals when allowed', () => {
    expect([parseNumberField('20', 20, 5000, true), parseNumberField(' 5000 ', 20, 5000, true), parseNumberField('48.5', 0, 96)]).toEqual([20, 5000, 48.5]);
  });
});

describe('server replies', () => {
  const library = {packs: [{id: 'a', name: 'A', revision: 1, builtin: true, accepted: true}], preferences, operations: [{id: 'o', status: 'running', phase: 'reference'}]};

  it('accepts a well-formed library and tolerates a missing operations list', () => {
    expect(parseCharacterLibrary(library).packs).toHaveLength(1);
    expect(parseCharacterLibrary({...library, operations: undefined}).operations).toEqual([]);
  });

  it.each([
    ['a non-object', 'nope'],
    ['a pack without a revision', {...library, packs: [{id: 'a', name: 'A', builtin: true, accepted: true}]}],
    ['preferences with a bad activity', {...library, preferences: {...preferences, activity: 'wild'}}],
    ['an operation with an unknown status', {...library, operations: [{id: 'o', status: 'melting', phase: 'reference'}]}],
  ])('rejects %s', (_, value) => {
    expect(() => parseCharacterLibrary(value)).toThrow(/unexpected/);
  });

  it('rejects pack and upload replies that are not the expected JSON', () => {
    expect(parseCharacterPack(JSON.stringify(library.packs[0])).id).toBe('a');
    expect(() => parseCharacterPack('<html>502</html>')).toThrow(/unexpected character pack reply/);
    expect(() => asCharacterPack({id: 'a'})).toThrow(/unexpected/);
    expect(parseArtifactId('{"id":"art"}')).toBe('art');
    expect(() => parseArtifactId('{"id":7}')).toThrow(/unexpected upload reply/);
    expect(() => parseArtifactId('')).toThrow(/unexpected upload reply/);
  });
});

describe('list helpers', () => {
  it('keeps the previous object when the new one is structurally equal', () => {
    const previous = {a: [1]};
    expect(keepIfSame(previous, {a: [1]})).toBe(previous);
    const next = {a: [2]};
    expect(keepIfSame(previous, next)).toBe(next);
  });

  it('lists live chats, then orphaned pets, then hidden ids that match nothing', () => {
    const sessions = [{id: 's1', title: 'First'}, {id: 's2', title: 'Old', archived: true}];
    const pets = [{...pet, id: 's1', session_id: 's1'}, {...pet, id: 'p2', session_id: 's2', name: 'Pip'}];
    const rows = assignmentRows(sessions, pets, {...preferences, hidden: ['s1', 'abcdefghijkl']});
    expect(rows).toEqual([
      {id: 's1', title: 'First'},
      {id: 'p2', title: 'Pip · Old'},
      {id: 'abcdefghijkl', title: 'Hidden agent abcdefgh'},
    ]);
  });
});
