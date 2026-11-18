import {describe, expect, it} from 'vitest';
import {applySiteEvent, defaultProfile, originLabel, otherSites, pinnedSites, reorderPins, type Site} from './sites';

const site = (id: string, over: Partial<Site> = {}): Site => ({
  id,
  project_id: 'p',
  origin: 'https://' + id + '.example.com',
  start_url: 'https://' + id + '.example.com/',
  title: id,
  profile_id: 'prof',
  open_mode: 'tab',
  pinned: true,
  position: 0,
  visit_count: 0,
  last_visited: null,
  frecency: 0,
  tab_id: null,
  note_count: 0,
  ...over,
});

describe('saved site ordering', () => {
  it('orders pinned sites by position, then title, and the rest by use', () => {
    const rows = [site('c', {position: 2}), site('a', {position: 1}), site('b', {position: 1}), site('z', {pinned: false, frecency: 5}), site('y', {pinned: false, frecency: 9})];
    expect(pinnedSites(rows).map(row => row.id)).toEqual(['a', 'b', 'c']);
    expect(otherSites(rows).map(row => row.id)).toEqual(['y', 'z']);
  });

  it('writes every row whose stored position is not its new index, because the host never shifts the others', () => {
    const pins = [site('a', {position: 0}), site('b', {position: 1}), site('c', {position: 2})];
    expect(reorderPins(pins, 2, 0)).toEqual([
      {id: 'c', position: 0},
      {id: 'a', position: 1},
      {id: 'b', position: 2},
    ]);
    expect(reorderPins(pins, 0, 1)).toEqual([
      {id: 'b', position: 0},
      {id: 'a', position: 1},
    ]);
    // Positions that were all equal (or had gaps) are normalised to the new order.
    const gaps = [site('a', {position: 0}), site('b', {position: 0}), site('c', {position: 7})];
    expect(reorderPins(gaps, 1, 0)).toEqual([
      {id: 'a', position: 1},
      {id: 'c', position: 2},
    ]);
  });

  it('writes nothing for a move that goes nowhere or off the list', () => {
    const pins = [site('a'), site('b', {position: 1})];
    expect(reorderPins(pins, 1, 1)).toEqual([]);
    expect(reorderPins(pins, 0, 5)).toEqual([]);
    expect(reorderPins(pins, -1, 0)).toEqual([]);
  });
});

describe('site events', () => {
  it('adds, replaces and removes by id, and ignores an event with no site', () => {
    const first = applySiteEvent([], {type: 'browser.site.upserted', id: 'a', site: site('a')});
    expect(first.map(row => row.id)).toEqual(['a']);
    const renamed = applySiteEvent(first, {type: 'browser.site.visited', id: 'a', site: site('a', {title: 'Renamed', visit_count: 3})});
    expect(renamed).toHaveLength(1);
    expect(renamed[0].title).toBe('Renamed');
    expect(applySiteEvent(renamed, {type: 'browser.site.upserted', id: null, site: null})).toBe(renamed);
    expect(applySiteEvent(renamed, {type: 'browser.site.removed', id: 'a', site: null})).toEqual([]);
  });
});

describe('profiles and labels', () => {
  it('prefers a lasting profile of the project, never a temporary one', () => {
    const profiles = [
      {id: 'temp', name: 'Temp', project_id: 'p', ephemeral: true},
      {id: 'other', name: 'Other', project_id: 'q', ephemeral: false},
      {id: 'mine', name: 'Mine', project_id: 'p', ephemeral: false},
    ];
    expect(defaultProfile(profiles, 'p')?.id).toBe('mine');
    expect(defaultProfile(profiles, 'z')?.id).toBe('other');
    expect(defaultProfile([profiles[0]], 'p')).toBeUndefined();
  });

  it('shows an origin without its scheme', () => {
    expect(originLabel('https://docs.example.com')).toBe('docs.example.com');
    expect(originLabel('http://127.0.0.1:8080')).toBe('127.0.0.1:8080');
  });
});
