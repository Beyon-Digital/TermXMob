import {useEffect, useRef, useState} from 'react';
import {ChevronDown, ChevronUp, Home, Settings} from 'lucide-react';
import CharacterSprite from '../components/CharacterSprite';
import {request} from '../lib/api';
import {isDetachedWindow, nativeWorkspace} from '../lib/native';
import {
  companionCapabilities,
  companionHome,
  listenCompanionCapabilities,
  listenCompanionOpen,
  type CompanionCapabilities,
} from '../lib/companion-native';
import {
  companionClip,
  errorMessage,
  habitatPosition,
  keepIfSame,
  parseCompanionPets,
  updateCompanions,
  type CompanionPet,
  type CompanionPreferences,
  type CompanionRuntime,
} from '../lib/characters';
import {usePolling} from '../lib/use-polling';
import {useSystemReducedMotion} from '../lib/use-system-reduced-motion';
import './habitat.css';

const POLL_MS = 2500;

interface DragState {
  id: number;
  start: number;
  x: number;
  moved: boolean;
}

interface WalkerProps {
  pet: CompanionPet;
  preferences: CompanionPreferences;
  index: number;
  home: number;
  onOpen: (id: string) => void;
  onMenu: (pet: CompanionPet) => void;
}

function Walker({pet, preferences, index, home, onOpen, onMenu}: WalkerProps) {
  const node = useRef<HTMLButtonElement>(null);
  const drag = useRef<DragState | null>(null);
  const position = useRef({x: 12 + index * 60, direction: index % 2 ? -1 : 1});
  const [dragging, setDragging] = useState(false);
  const [direction, setDirection] = useState(index % 2 ? -1 : 1);
  const [footOffset, setFootOffset] = useState(0);
  const systemReduced = useSystemReducedMotion();
  const reduced = preferences.reduced_motion || systemReduced;
  const walking = !reduced && pet.state === 'idle' && !pet.action;
  const size = Math.min(preferences.size, 100);
  const homeAction = pet.action?.name === 'home' ? pet.action.id : null;

  useEffect(() => {
    position.current = {x: 12 + index * 60, direction: index % 2 ? -1 : 1};
    setDirection(position.current.direction);
    if (node.current) node.current.style.transform = 'translateX(' + position.current.x + 'px)';
  }, [home, index, homeAction]);

  useEffect(() => {
    const button = node.current;
    if (!button) return;
    let animation = 0;
    let last = performance.now();
    let facing = position.current.direction;
    const speed = preferences.activity === 'playful' ? 30 : 12;
    const move = (now: number) => {
      const elapsed = Math.min(64, now - last);
      last = now;
      if (!drag.current && walking) {
        const next = habitatPosition(position.current.x, position.current.direction, elapsed, button.parentElement?.clientWidth || 320, size, speed);
        position.current = next;
        button.style.transform = 'translateX(' + next.x + 'px)';
        if (next.direction !== facing) {
          facing = next.direction;
          setDirection(facing);
        }
      }
      if (!document.hidden) animation = requestAnimationFrame(move);
    };
    const changed = () => {
      cancelAnimationFrame(animation);
      last = performance.now();
      if (!document.hidden && walking) animation = requestAnimationFrame(move);
    };
    if (walking) animation = requestAnimationFrame(move);
    document.addEventListener('visibilitychange', changed);
    return () => {
      cancelAnimationFrame(animation);
      document.removeEventListener('visibilitychange', changed);
    };
  }, [walking, size, preferences.activity]);

  return (
    <button
      type="button"
      ref={node}
      className="habitat-walker"
      style={{bottom: 4 - footOffset}}
      title={pet.name + ' · ' + pet.state + ' · Drag to move, click to open conversation'}
      aria-label={'Open ' + pet.name + ', ' + pet.state}
      onClick={event => {
        if (drag.current?.moved) {
          event.preventDefault();
          drag.current = null;
          return;
        }
        onOpen(pet.session_id);
      }}
      onContextMenu={event => {
        event.preventDefault();
        onMenu(pet);
      }}
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = {id: event.pointerId, start: event.clientX, x: position.current.x, moved: false};
        setDragging(true);
      }}
      onPointerMove={event => {
        const current = drag.current;
        if (!current || current.id !== event.pointerId) return;
        const width = event.currentTarget.parentElement?.clientWidth || 320;
        const x = Math.min(Math.max(0, current.x + event.clientX - current.start), Math.max(0, width - size));
        current.moved ||= Math.abs(event.clientX - current.start) > 4;
        position.current.x = x;
        event.currentTarget.style.transform = 'translateX(' + x + 'px)';
      }}
      onPointerUp={event => {
        if (drag.current?.id !== event.pointerId) return;
        event.currentTarget.releasePointerCapture(event.pointerId);
        setDragging(false);
        if (!drag.current.moved) drag.current = null;
        else setTimeout(() => (drag.current = null), 0);
      }}
      onPointerCancel={() => {
        drag.current = null;
        setDragging(false);
      }}
    >
      <CharacterSprite
        packId={pet.pack_id}
        revision={pet.pack_revision}
        state={dragging ? 'drag' : companionClip(pet, walking)}
        size={size}
        flip={direction < 0}
        reducedMotion={reduced}
        onManifest={value => setFootOffset(((value.logical_size.height - value.foot_anchor.y) * size) / Math.max(value.logical_size.width, value.logical_size.height))}
      />
      <span>{pet.name}</span>
    </button>
  );
}

export default function CompanionHabitat({onOpen, onSettings}: {onOpen: (sessionId: string) => void; onSettings: () => void}) {
  const [runtime, setRuntime] = useState<CompanionRuntime | null>(null);
  const [capabilities, setCapabilities] = useState<CompanionCapabilities | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [home, setHome] = useState(0);
  const [menu, setMenu] = useState<CompanionPet | null>(null);
  const [error, setError] = useState('');
  // Bumping this restarts the polling loop, which fetches at once.
  const [refreshToken, setRefreshToken] = useState(0);
  const open = useRef(onOpen);
  useEffect(() => {
    open.current = onOpen;
  });
  const detached = nativeWorkspace() && isDetachedWindow();

  // Pet state has no push channel, so this keeps polling; usePolling pauses while the tab is hidden and slows down while nothing changes.
  usePolling(
    async signal => {
      const value = await request<CompanionRuntime>('/api/companions/runtime', {signal});
      return {...value, pets: parseCompanionPets(value)};
    },
    {
      interval: POLL_MS,
      enabled: !detached,
      resetKey: refreshToken,
      onResult: value => {
        setRuntime(previous => keepIfSame(previous, value));
        setError('');
      },
      onError: reason => {
        setRuntime(null);
        setError(errorMessage(reason));
      },
    },
  );

  useEffect(() => {
    if (detached) return;
    let active = true;
    let release: (() => void) | undefined;
    let releaseCapabilities: (() => void) | undefined;
    companionCapabilities()
      .then(value => {
        if (active) setCapabilities(value);
      })
      .catch(() => {
        if (active) setCapabilities({desktop: false, backend: 'in-app'});
      });
    listenCompanionCapabilities(value => {
      if (active) setCapabilities(value);
    })
      .then(value => {
        if (active) releaseCapabilities = value;
        else value();
      })
      .catch(reason => {
        if (active) setError(errorMessage(reason));
      });
    listenCompanionOpen(id => open.current(id))
      .then(value => {
        if (active) release = value;
        else value();
      })
      .catch(reason => {
        if (active) setError(errorMessage(reason));
      });
    const refresh = () => setRefreshToken(value => value + 1);
    const reset = () => setHome(value => value + 1);
    window.addEventListener('termx-companions-changed', refresh);
    window.addEventListener('termx-companion-home', reset);
    return () => {
      active = false;
      release?.();
      releaseCapabilities?.();
      window.removeEventListener('termx-companions-changed', refresh);
      window.removeEventListener('termx-companion-home', reset);
    };
  }, [detached]);

  if (!runtime?.enabled || !runtime.preferences.enabled || !capabilities) return null;
  const roaming = runtime.pets.filter(pet => pet.roaming);
  const docked = runtime.pets.filter(pet => !pet.roaming);
  const visible = capabilities.desktop ? docked : roaming;
  const preferences = runtime.preferences;
  const change = async (changes: Partial<CompanionPreferences>) => {
    try {
      await updateCompanions(changes);
      setMenu(null);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  return (
    <aside className={'companion-habitat ' + (capabilities.desktop ? 'companion-dock' : 'companion-in-app')} aria-label={capabilities.desktop ? 'Character home dock' : 'Character habitat'}>
      <header>
        <button type="button" className="companion-home-title" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
          <Home size={13} />
          <span>{capabilities.desktop ? 'Character home' : 'Your characters'}</span>
          {expanded ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
        <button type="button" aria-label="Bring pets home" onClick={() => void companionHome().catch(reason => setError(errorMessage(reason)))}>
          <Home size={13} />
        </button>
        <button type="button" aria-label="Character settings" onClick={onSettings}>
          <Settings size={13} />
        </button>
      </header>
      {expanded && (
        <>
          {!capabilities.desktop && (
            <div className="habitat-floor">
              {visible.map((pet, index) => (
                <Walker key={pet.id} pet={pet} index={index} preferences={preferences} home={home} onOpen={onOpen} onMenu={setMenu} />
              ))}
              {!visible.length && (
                <p>
                  Start a conversation.
                  <br />
                  Its character will join you here.
                </p>
              )}
            </div>
          )}
          {docked.length > 0 && (
            <div className="habitat-docked">
              {docked.map(pet => (
                <button
                  type="button"
                  key={pet.id}
                  aria-label={'Open ' + pet.name + ', ' + pet.state}
                  onClick={() => onOpen(pet.session_id)}
                  onContextMenu={event => {
                    event.preventDefault();
                    setMenu(pet);
                  }}
                >
                  <CharacterSprite packId={pet.pack_id} revision={pet.pack_revision} state={pet.state} size={42} reducedMotion={preferences.reduced_motion} />
                  <span>
                    {pet.name}
                    <small>{pet.state}</small>
                  </span>
                </button>
              ))}
            </div>
          )}
          {capabilities.desktop && !docked.length && <p className="habitat-empty">Your characters are exploring.</p>}
        </>
      )}
      {menu && (
        <div className="companion-menu" role="group" aria-label={'Actions for ' + menu.name}>
          <strong>{menu.name}</strong>
          <button
            type="button"
            onClick={() => {
              onOpen(menu.session_id);
              setMenu(null);
            }}
          >
            Open conversation
          </button>
          <button type="button" onClick={() => void change({pinned: preferences.pinned.includes(menu.id) ? preferences.pinned.filter(id => id !== menu.id) : [...preferences.pinned, menu.id]})}>
            {preferences.pinned.includes(menu.id) ? 'Unpin character' : 'Pin character'}
          </button>
          <button type="button" onClick={() => void change({hidden: [...preferences.hidden, menu.id]})}>
            Hide character
          </button>
          <button type="button" onClick={() => void change({enabled: false})}>
            Pause all characters
          </button>
          <button type="button" onClick={onSettings}>
            Choose a character
          </button>
          <button type="button" onClick={() => setMenu(null)}>
            Close menu
          </button>
        </div>
      )}
      {error && (
        <small role="alert" className="inline-error">
          {error}
        </small>
      )}
    </aside>
  );
}
