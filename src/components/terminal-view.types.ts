export type TerminalHandle = {
  write: (data: string | Uint8Array) => void;
  reset: () => void;
  fit: () => void;
  blur: () => void;
};

export type TerminalViewProps = {
  embedUrl: string;
  onInput: (data: string) => void;
  onResize: (cols: number, rows: number) => void;
  onFocus?: () => void;
};
