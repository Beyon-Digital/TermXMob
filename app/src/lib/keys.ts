export const EXTRA_KEY_ROWS = [
  ["ESC", "TAB", "CTRL", "ALT", "INT", "BKSP", "CLR", "KB"],
  ["HOME", "UP", "END", "PGUP", "LEFT", "DOWN", "RIGHT", "PGDN"],
] as const;

export const REPEAT_KEYS = new Set(["BKSP"]);

const GLYPHS: Record<string, string> = {
  ESC: "⎋",
  TAB: "⇥",
  CTRL: "⌃",
  ALT: "⌥",
  UP: "↑",
  DOWN: "↓",
  LEFT: "←",
  RIGHT: "→",
  HOME: "↖",
  END: "↘",
  PGUP: "⇞",
  PGDN: "⇟",
  BKSP: "⌫",
  INT: "^C",
  CLR: "⌧",
  KB: "⌄",
};

const A11Y_LABELS: Record<string, string> = {
  ESC: "Escape",
  TAB: "Tab",
  CTRL: "Control",
  ALT: "Alt",
  UP: "Arrow up",
  DOWN: "Arrow down",
  LEFT: "Arrow left",
  RIGHT: "Arrow right",
  HOME: "Home",
  END: "End",
  PGUP: "Page up",
  PGDN: "Page down",
  BKSP: "Backspace",
  INT: "Interrupt",
  CLR: "Clear line",
  KB: "Hide keyboard",
};

export function keyGlyph(name: string): string {
  return GLYPHS[name] ?? name;
}

export function isGlyphKey(name: string): boolean {
  return name in GLYPHS;
}

export function keyA11yLabel(name: string): string {
  return A11Y_LABELS[name] ?? name;
}

const SEQUENCES: Record<string, string> = {
  ESC: "\x1b",
  TAB: "\t",
  "/": "/",
  "-": "-",
  UP: "\x1b[A",
  DOWN: "\x1b[B",
  RIGHT: "\x1b[C",
  LEFT: "\x1b[D",
  HOME: "\x1b[H",
  END: "\x1b[F",
  PGUP: "\x1b[5~",
  PGDN: "\x1b[6~",
  BKSP: "\x7f",
  INT: "\x03",
  CLR: "\x15",
};

export function ctrlChar(input: string): string {
  if (input.length !== 1) return input;
  const code = input.toLowerCase().charCodeAt(0);
  if (code >= 97 && code <= 122) return String.fromCharCode(code - 96);
  return input;
}

export function sequenceForKey(name: string): string {
  return SEQUENCES[name] ?? name;
}
