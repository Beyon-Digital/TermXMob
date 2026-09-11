import { useEffect, useRef, type CSSProperties } from "react";

import { useAppTheme } from "@/hooks/use-app-theme";
import {
  ctrlChar,
  EXTRA_KEY_ROWS,
  REPEAT_KEYS,
  isGlyphKey,
  keyA11yLabel,
  keyGlyph,
  sequenceForKey,
} from "@/lib/keys";

type Props = {
  ctrlOn: boolean;
  altOn: boolean;
  onCtrl: () => void;
  onAlt: () => void;
  onInput: (data: string) => void;
  onSignal?: (name: string) => void;
};

export function ExtraKeys({ ctrlOn, altOn, onCtrl, onAlt, onInput, onSignal }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const hold = useRef<{ delay?: ReturnType<typeof setTimeout>; next?: ReturnType<typeof setTimeout> }>({});

  const stopHold = () => {
    if (hold.current.delay) clearTimeout(hold.current.delay);
    if (hold.current.next) clearTimeout(hold.current.next);
    hold.current = {};
  };

  useEffect(() => stopHold, []);

  const fire = (name: string) => {
    if (name === "CTRL") {
      onCtrl();
      return;
    }
    if (name === "ALT") {
      onAlt();
      return;
    }
    if (name === "INT") {
      onSignal?.("int");
      onInput("\x03");
      return;
    }
    let seq = sequenceForKey(name);
    if (ctrlOn) {
      seq = ctrlChar(seq);
      onCtrl();
    }
    if (altOn) {
      seq = `\x1b${seq}`;
      onAlt();
    }
    onInput(seq);
  };

  const startHold = (name: string) => {
    stopHold();
    fire(name);
    if (!REPEAT_KEYS.has(name)) return;
    const seq = sequenceForKey(name);
    const tick = (ms: number) => {
      hold.current.next = setTimeout(() => {
        onInput(seq);
        tick(Math.max(32, ms * 0.85));
      }, ms);
    };
    hold.current.delay = setTimeout(() => tick(80), 400);
  };

  return (
    <div
      style={{
        ...wrap,
        background: ui.surface,
        borderTop: `1px solid ${ui.border}`,
      }}>
      {EXTRA_KEY_ROWS.map((row, i) => (
        <div key={i} style={rowStyle}>
          {row.map((name) => {
            const active = (name === "CTRL" && ctrlOn) || (name === "ALT" && altOn);
            const modifier = name === "CTRL" || name === "ALT";
            return (
              <button
                key={name}
                type="button"
                title={keyA11yLabel(name)}
                aria-label={keyA11yLabel(name)}
                aria-pressed={modifier ? active : undefined}
                onPointerDown={(e) => {
                  e.preventDefault();
                  startHold(name);
                }}
                onPointerUp={stopHold}
                onPointerCancel={stopHold}
                onPointerLeave={stopHold}
                style={{
                  ...btn,
                  fontSize: isGlyphKey(name) ? 16 : 12,
                  background: active ? ui.accent : modifier ? ui.surfaceActive : ui.surfaceAlt,
                  color: active ? ui.accentText : ui.text,
                }}>
                {keyGlyph(name)}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

const wrap: CSSProperties = {
  position: "relative",
  zIndex: 50,
  padding: "6px 6px 10px",
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const rowStyle: CSSProperties = {
  display: "flex",
  gap: 4,
};

const btn: CSSProperties = {
  flex: 1,
  minHeight: 40,
  border: 0,
  borderRadius: 6,
  cursor: "pointer",
  touchAction: "manipulation",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};
