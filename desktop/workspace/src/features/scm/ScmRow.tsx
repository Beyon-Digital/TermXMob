import {File, FileCode2, FileJson, FileText, Image, Minus, Plus, type LucideIcon} from 'lucide-react';
import type {ScmEntry} from './scm-types';

const BY_EXTENSION: Record<string, LucideIcon> = {
  ts: FileCode2,
  tsx: FileCode2,
  js: FileCode2,
  jsx: FileCode2,
  mjs: FileCode2,
  py: FileCode2,
  rs: FileCode2,
  go: FileCode2,
  java: FileCode2,
  rb: FileCode2,
  c: FileCode2,
  h: FileCode2,
  cpp: FileCode2,
  css: FileCode2,
  html: FileCode2,
  sh: FileCode2,
  json: FileJson,
  toml: FileJson,
  yaml: FileJson,
  yml: FileJson,
  md: FileText,
  txt: FileText,
  png: Image,
  jpg: Image,
  jpeg: Image,
  gif: Image,
  svg: Image,
  webp: Image,
};

export function iconFor(name: string): LucideIcon {
  const dot = name.lastIndexOf('.');
  return (dot > 0 && BY_EXTENSION[name.slice(dot + 1).toLowerCase()]) || File;
}

type Props = {
  entry: ScmEntry;
  busy: boolean;
  /** Row click: the diff, or the file itself for a conflict. */
  onOpen: (entry: ScmEntry) => void;
  onOpenFile: (path: string) => void;
  onStage: (entry: ScmEntry) => void;
  onUnstage: (entry: ScmEntry) => void;
};

/** One changed file: a 32px row. Its actions show on hover and keyboard focus (always on touch). */
export default function ScmRow({entry, busy, onOpen, onOpenFile, onStage, onUnstage}: Props) {
  const {file, path} = entry;
  const Icon = iconFor(entry.name);
  const hasFile = entry.letter !== 'D' && file.reviewable;
  const unstage = entry.side === 'staged';
  return (
    <li className="scm-file" data-side={entry.side}>
      <button
        type="button"
        className="scm-file-main"
        data-scm-file
        title={file.reviewable ? path : 'This file points outside the checkout'}
        disabled={!file.reviewable}
        onClick={() => onOpen(entry)}
      >
        <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
        <span className="scm-file-name">{entry.name}</span>
        {entry.dir && <span className="scm-file-dir">{entry.dir}</span>}
        <span className="visually-hidden">, {entry.word}</span>
      </button>
      <span className="scm-file-actions">
        {hasFile && (
          <button type="button" className="scm-icon" aria-label={'Open file ' + path} title="Open file" onClick={() => onOpenFile(path)}>
            <FileText size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
        {unstage ? (
          <button type="button" className="scm-icon" aria-label={'Unstage ' + path} title="Unstage" disabled={busy} onClick={() => onUnstage(entry)}>
            <Minus size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            className="scm-icon"
            aria-label={(entry.side === 'conflict' ? 'Mark as resolved ' : 'Stage ') + path}
            title={entry.side === 'conflict' ? 'Mark as resolved' : 'Stage'}
            disabled={busy}
            onClick={() => onStage(entry)}
          >
            <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </span>
      <span className="scm-letter" data-letter={entry.letter} aria-hidden="true">
        {entry.letter}
      </span>
    </li>
  );
}
