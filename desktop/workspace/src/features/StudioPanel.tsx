import {useEffect, useId, useRef, useState, type ReactNode, type KeyboardEvent} from 'react';
import {X} from 'lucide-react';
import {Button} from '../components/ui/button';

type Props = {open: boolean; title: string; description?: string; inline?: boolean; onClose: () => void; children: ReactNode; className?: string; initialFocus?: string};

/** Native dialogs retain drafts while the browser provides modal focus and Escape handling. */
export default function StudioPanel({open, title, description, inline = false, onClose, children, className = '', initialFocus}: Props) {
  const id = useId();
  const node = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const previousInline = useRef(inline);
  const [present, setPresent] = useState(open);
  const openNow = useRef(open); openNow.current = open;
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function stopClose() {if (closeTimer.current !== null) {clearTimeout(closeTimer.current); closeTimer.current = null;}}
  function exitDuration(dialog: HTMLDialogElement) {
    if (inline || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return 0;
    const value = getComputedStyle(dialog).getPropertyValue('--motion-exit').trim();
    const duration = Number.parseFloat(value);
    return Number.isFinite(duration) ? Math.max(0, value.endsWith('ms') ? duration : duration * 1000) : 120;
  }
  function usable(element: HTMLElement) {
    if (!element.isConnected || element.closest('[hidden],[inert],[aria-hidden="true"]') || element.matches(':disabled')) return false;
    for (let current: HTMLElement | null = element; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  }
  useEffect(() => {
    const dialog = node.current;
    if (!dialog) return;
    stopClose();
    const switchingPresentation = previousInline.current !== inline;
    previousInline.current = inline;
    if (dialog.open && switchingPresentation) {
      if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
    }
    if (open) {
      setPresent(true);
      if (!dialog.open) {
        const focused = document.activeElement;
        opener.current = !inline && focused instanceof HTMLElement && focused !== document.body && !dialog.contains(focused) ? focused : null;
        if (inline && typeof dialog.show === 'function') dialog.show();
        else if (!inline && typeof dialog.showModal === 'function') dialog.showModal();
        else {dialog.setAttribute('open', ''); if (!inline) dialog.querySelector<HTMLElement>('button,input,select,textarea,summary,[tabindex]')?.focus();}
      }
      if (initialFocus) dialog.querySelector<HTMLElement>(initialFocus)?.focus();
    } else if (dialog.open) {
      const finish = () => {
        closeTimer.current = null;
        if (openNow.current) return;
        const active = document.activeElement;
        const restore = !inline && (active === document.body || active === dialog || dialog.contains(active));
        if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
        setPresent(false);
        if (restore && opener.current && usable(opener.current)) opener.current.focus();
      };
      const duration = exitDuration(dialog);
      if (duration) closeTimer.current = setTimeout(finish, duration); else finish();
    } else setPresent(false);
    return stopClose;
  }, [open, inline, initialFocus]);
  useEffect(() => () => stopClose(), []);
  function containFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (!open || inline || event.key !== 'Tab') return;
    const dialog = event.currentTarget;
    const candidates = Array.from(dialog.querySelectorAll<HTMLElement>('a[href],button,input,select,textarea,summary,[tabindex]')).filter(element => {
      if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[hidden],[inert]')) return false;
      for (let current: HTMLElement | null = element; current && current !== dialog; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
      }
      return true;
    });
    const first = candidates[0], last = candidates[candidates.length - 1];
    if (!first) {event.preventDefault(); dialog.focus(); return;}
    const focused = document.activeElement;
    if (event.shiftKey && (focused === first || !candidates.includes(focused as HTMLElement))) {event.preventDefault(); last.focus();}
    else if (!event.shiftKey && (focused === last || !candidates.includes(focused as HTMLElement))) {event.preventDefault(); first.focus();}
  }
  return <dialog ref={node} tabIndex={-1} onKeyDown={containFocus} hidden={!open && !present} inert={!open} aria-hidden={!open ? true : undefined} data-state={open ? 'open' : 'closed'} role={inline ? 'region' : 'dialog'} aria-modal={!inline && open ? true : undefined}
    aria-labelledby={id + '-title'} aria-describedby={description ? id + '-description' : undefined}
    className={'studio-panel' + (inline ? ' studio-panel-inline' : '') + (className ? ' ' + className : '')}
    onClickCapture={event => {if (!open) {event.preventDefault(); event.stopPropagation();}}}
    onSubmitCapture={event => {if (!open) {event.preventDefault(); event.stopPropagation();}}}
    onKeyDownCapture={event => {if (!open) {event.preventDefault(); event.stopPropagation();}}}
    onCancel={event => {event.preventDefault(); if (open) onClose();}}
    onClick={event => {if (open && !inline && event.target === event.currentTarget) {const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();}}}>
    <header className="studio-panel-header"><div><h2 id={id + '-title'}>{title}</h2>{description && <p id={id + '-description'}>{description}</p>}</div>{!inline && <Button variant="ghost" size="icon" aria-label={'Close ' + title} onClick={onClose}><X size={17}/></Button>}</header>
    <div className="studio-panel-content">{children}</div>
  </dialog>;
}
