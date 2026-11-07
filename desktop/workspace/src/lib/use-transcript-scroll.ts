import {useEffect, useRef, useState} from 'react';
import {measureElement, useVirtualizer} from '@tanstack/react-virtual';
import {json, request, type Session} from './api';
import type {ConversationMessageRow} from './conversation-messages';
import {anchorCorrection, conversationAnchor, type ConversationAnchor} from './conversation-position';
import {estimateMessageHeight} from './message-size';
import {captureSessionWrite, serializeSessionWrite} from './session-writes';

/** Within this distance of the end the reader counts as "following" the conversation. */
const PIN_DISTANCE = 64;
/** The "Jump to latest" button appears beyond this distance. */
const AWAY_DISTANCE = 350;
const SAVE_DELAY = 700;
const RESTORE_FRAMES = 24;

interface Options {
  session: Session | null;
  messages: ConversationMessageRow[];
  onError: (error: unknown) => void;
  /** Changes while the newest message streams in, so a following reader stays at the end. */
  followKey?: unknown;
}

/**
 * Scroll behaviour of the transcript: virtualised rows, scroll restoration per conversation, a debounced
 * save of the reading position, and follow-the-end that never pulls a reader who scrolled up back down.
 */
export function useTranscriptScroll({session, messages, onError, followKey}: Options) {
  const scroller = useRef<HTMLDivElement>(null);
  const offsets = useRef(new Map<string, number>());
  const anchors = useRef(new Map<string, ConversationAnchor | null>());
  const pinned = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const latestMessages = useRef(messages);
  const onErrorRef = useRef(onError);
  useEffect(() => { latestMessages.current = messages; onErrorRef.current = onError; });

  const virtual = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scroller.current,
    estimateSize: index => estimateMessageHeight(latestMessages.current[index]),
    // A row measured at 0 is hidden (display:none panel), not empty. Keep the last known size.
    measureElement: (element, entry, instance) => {
      const size = measureElement(element, entry, instance);
      if (size > 0) return size;
      const index = instance.indexFromElement(element);
      return instance.itemSizeCache.get(instance.options.getItemKey(index)) ?? instance.options.estimateSize(index);
    },
    overscan: 8,
    initialRect: {width: 800, height: 600},
    getItemKey: index => messages[index].id,
  });

  useEffect(() => {
    if (!session?.id) return;
    const id = session.id, element = scroller.current;
    if (!element) return;
    let timer: ReturnType<typeof setTimeout> | undefined, restoreFrame = 0, scrollFrame = 0, restoring = true, attempts = 0;
    const anchor = anchors.current.get(id) ?? session.scroll_anchor;
    const offset = offsets.current.get(id) ?? session.scroll;
    const distance = () => element.scrollHeight - element.scrollTop - element.clientHeight;
    // A conversation opened with no saved position follows the end; one with a position stays where it was.
    pinned.current = !(anchor || offsets.current.has(id) || offset > 0);
    const restore = () => {
      if (!restoring) return;
      const rows = latestMessages.current;
      if (attempts++ === 0) {
        const index = anchor ? rows.findIndex(message => message.id === anchor.id) : -1;
        if (index >= 0) virtual.scrollToIndex(index, {align: 'start'});
        else if (offsets.current.has(id) || offset > 0) virtual.scrollToOffset(offset);
        else if (rows.length) virtual.scrollToIndex(rows.length - 1, {align: 'end'});
      } else if (anchor) {
        const correction = anchorCorrection(element, anchor);
        if (correction !== null && Math.abs(correction) > 1) virtual.scrollToOffset(element.scrollTop + correction);
      }
      if (attempts < RESTORE_FRAMES) restoreFrame = requestAnimationFrame(restore);
      else { restoring = false; pinned.current = distance() <= PIN_DISTANCE; setAwayFromLatest(distance() > AWAY_DISTANCE); }
    };
    restoreFrame = requestAnimationFrame(restore);
    const cancelRestore = () => { restoring = false; cancelAnimationFrame(restoreFrame); };
    // The anchor query walks the DOM, so it runs at most once per frame however many scroll events arrive.
    const remember = () => {
      scrollFrame = 0;
      const top = element.scrollTop, position = conversationAnchor(element);
      offsets.current.set(id, top);
      anchors.current.set(id, position);
      clearTimeout(timer);
      const intent = captureSessionWrite();
      timer = setTimeout(async () => {
        try {
          await serializeSessionWrite(id, async () => {
            const current = await request<Session>('/api/workspace/sessions/' + id + '?turns=false', {signal: intent.signal});
            intent.assertCurrent();
            await request('/api/workspace/sessions/' + id, {...json('PATCH', {revision: current.revision, changes: {scroll: top, scroll_anchor: position}}), signal: intent.signal});
          }, intent);
        } catch (error) { if (intent.isCurrent()) onErrorRef.current(error); } finally { intent.release(); }
      }, SAVE_DELAY);
    };
    const onScroll = () => {
      const away = distance();
      if (!restoring) pinned.current = away <= PIN_DISTANCE;
      setAwayFromLatest(away > AWAY_DISTANCE);
      if (!restoring && !scrollFrame) scrollFrame = requestAnimationFrame(remember);
    };
    const interactions = ['wheel', 'pointerdown', 'touchstart', 'keydown'];
    element.addEventListener('scroll', onScroll, {passive: true});
    for (const event of interactions) element.addEventListener(event, cancelRestore, {passive: true});
    return () => {
      cancelAnimationFrame(restoreFrame);
      cancelAnimationFrame(scrollFrame);
      clearTimeout(timer);
      offsets.current.set(id, element.scrollTop);
      anchors.current.set(id, conversationAnchor(element));
      element.removeEventListener('scroll', onScroll);
      for (const event of interactions) element.removeEventListener(event, cancelRestore);
    };
  }, [session?.id]);

  // New rows and streaming text move the end. Follow it only while the reader is already there.
  useEffect(() => {
    if (messages.length && pinned.current) virtual.scrollToIndex(messages.length - 1, {align: 'end'});
  }, [messages.length, followKey]);

  return {
    scroller,
    virtual,
    awayFromLatest,
    /** Used when the user sends a message or presses "Jump to latest". */
    scrollToLatest() {
      pinned.current = true;
      if (messages.length) virtual.scrollToIndex(messages.length - 1, {align: 'end'});
      setAwayFromLatest(false);
    },
    scrollToMessage(index: number) {
      virtual.scrollToIndex(index, {align: 'end'});
      setAwayFromLatest(false);
    },
  };
}
