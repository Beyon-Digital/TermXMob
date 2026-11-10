import {useEffect, useRef, useState, type RefObject} from 'react';
import {request} from '../lib/api';
import {transfer} from '../lib/transfers';
import {characterDimensions, errorMessage, characterPath, clipFor, frameAt, frameDelay, type CharacterManifest} from '../lib/characters';
import {useSystemReducedMotion} from '../lib/use-system-reduced-motion';
import './character-sprite.css';

type Assets = {manifest: CharacterManifest; images: HTMLImageElement[]};
type Lease = {users: number; urls: string[]};
type CacheEntry = {lease: Lease; promise: Promise<Assets>};
const cache = new Map<string, CacheEntry>();

function decode(url: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Could not decode character artwork.'));
    image.src = url;
  });
}

async function load(packId: string, revision: number, lease: Lease): Promise<Assets> {
  const manifest = await request<CharacterManifest>(characterPath(packId) + '/manifest?revision=' + revision);
  const images = await Promise.all(
    manifest.atlases.map(async atlas => {
      const blob = await transfer(characterPath(packId) + '/assets/' + encodeURIComponent(atlas.file) + '?revision=' + revision);
      if (!lease.users) throw new Error('Character artwork was closed.');
      const url = URL.createObjectURL(blob);
      lease.urls.push(url);
      return decode(url);
    }),
  );
  return {manifest, images};
}

function release(key: string, entry: CacheEntry) {
  entry.lease.users--;
  if (entry.lease.users) return;
  cache.delete(key);
  void entry.promise
    .finally(() => entry.lease.urls.forEach(url => URL.revokeObjectURL(url)))
    .catch(() => {});
}

function acquire(packId: string, revision: number) {
  const key = packId + ':' + revision;
  const current = cache.get(key);
  if (current) {
    current.lease.users++;
    return {key, entry: current};
  }
  const lease: Lease = {users: 1, urls: []};
  const entry: CacheEntry = {lease, promise: load(packId, revision, lease)};
  cache.set(key, entry);
  return {key, entry};
}

/** Loads a pack's manifest and artwork. Pass `enabled: false` to wait, for example until the sprite scrolls into view. */
export function useCharacterManifest(packId: string, revision: number, enabled = true) {
  const [assets, setAssets] = useState<Assets | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setAssets(null);
    setError('');
    if (!enabled || !packId || !Number.isInteger(revision) || revision < 1) return;
    const {key, entry} = acquire(packId, revision);
    entry.promise
      .then(value => {
        if (active) setAssets(value);
      })
      .catch(reason => {
        if (active) setError(errorMessage(reason));
      });
    return () => {
      active = false;
      release(key, entry);
    };
  }, [packId, revision, enabled]);
  return {assets, error};
}

function useNearViewport(node: RefObject<HTMLElement | null>, lazy: boolean) {
  const [near, setNear] = useState(!lazy || typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const target = node.current;
    if (near || !target) return;
    const observer = new IntersectionObserver(
      entries => {
        if (entries.some(entry => entry.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      {rootMargin: '120px'},
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [node, near]);
  return near;
}

interface Props {
  packId: string;
  revision: number;
  state?: string;
  size?: number;
  /** Show this frame and stop animating. */
  frame?: number;
  paused?: boolean;
  reducedMotion?: boolean;
  flip?: boolean;
  /** Fetch artwork only once the sprite is near the viewport. */
  lazy?: boolean;
  onManifest?: (value: CharacterManifest) => void;
}

export default function CharacterSprite({packId, revision, state = 'idle', size = 96, frame, paused = false, reducedMotion = false, flip = false, lazy = false, onManifest}: Props) {
  const systemReduced = useSystemReducedMotion();
  const still = reducedMotion || systemReduced;
  const wrapper = useRef<HTMLSpanElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const near = useNearViewport(wrapper, lazy);
  const {assets, error} = useCharacterManifest(packId, revision, near);
  const notify = useRef(onManifest);
  useEffect(() => {
    notify.current = onManifest;
  });
  useEffect(() => {
    if (assets) notify.current?.(assets.manifest);
  }, [assets]);

  const logical = assets?.manifest.logical_size ?? {width: 1, height: 1};
  const dimensions = characterDimensions(logical, size);
  const ratio = Math.min(2, window.devicePixelRatio || 1);

  // Resizing a canvas clears it, so size it in its own effect and let the drawing effect repaint after.
  useEffect(() => {
    const node = canvas.current;
    if (!node || !assets) return;
    node.width = Math.ceil(dimensions.width * ratio);
    node.height = Math.ceil(dimensions.height * ratio);
  }, [assets, dimensions.width, dimensions.height, ratio]);

  useEffect(() => {
    const node = canvas.current;
    const context = node?.getContext('2d');
    if (!node || !context || !assets) return;
    const {manifest, images} = assets;
    const clip = clipFor(manifest, state);
    if (!clip) return;
    const scale = ratio * dimensions.scale;
    let drawn = -1;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let started = performance.now();
    let hiddenAt = 0;

    function paint(index: number) {
      if (!context || !node || index === drawn) return;
      drawn = index;
      context.clearRect(0, 0, node.width, node.height);
      const current = clip?.frames[index];
      if (!current) return;
      const atlas = typeof current.atlas === 'number' ? current.atlas : manifest.atlases.findIndex(entry => entry.file === current.atlas);
      const image = images[atlas];
      if (!image) return;
      context.save();
      context.scale(scale, scale);
      if (flip) {
        context.translate(manifest.logical_size.width, 0);
        context.scale(-1, 1);
      }
      context.drawImage(image, current.x, current.y, current.width, current.height, 0, 0, manifest.logical_size.width, manifest.logical_size.height);
      context.restore();
    }

    // Each timer fires when the next frame is due, so a still or single-frame clip draws once and then does nothing.
    function tick() {
      const elapsed = performance.now() - started;
      paint(frameAt(clip, elapsed));
      const delay = frameDelay(clip, elapsed);
      if (delay !== null && !document.hidden) timer = setTimeout(tick, delay);
    }

    const lastFrame = Math.max(0, clip.frames.length - 1);
    if (frame !== undefined) paint(Math.min(Math.max(0, frame), lastFrame));
    else if (paused || still) paint(0);
    else {
      tick();
      const visibility = () => {
        clearTimeout(timer);
        if (document.hidden) {
          hiddenAt = performance.now();
          return;
        }
        if (hiddenAt) started += performance.now() - hiddenAt;
        hiddenAt = 0;
        tick();
      };
      document.addEventListener('visibilitychange', visibility);
      return () => {
        clearTimeout(timer);
        document.removeEventListener('visibilitychange', visibility);
      };
    }
    return undefined;
  }, [assets, state, frame, paused, still, flip, ratio, dimensions.scale, dimensions.width, dimensions.height]);

  return (
    <span ref={wrapper} className="character-sprite" style={{width: dimensions.width, height: dimensions.height}}>
      {error ? (
        <span className="character-art-error" role="img" aria-label={error}>
          ?
        </span>
      ) : (
        <canvas ref={canvas} style={{width: dimensions.width, height: dimensions.height}} role="img" aria-label={assets ? assets.manifest.name + ' ' + state : 'Loading character artwork'} />
      )}
    </span>
  );
}
