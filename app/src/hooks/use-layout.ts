import { useEffect, useState } from "react";
import { Platform, useWindowDimensions } from "react-native";

export const WIDE_MIN_WIDTH = 1000;
export const COMPACT_MAX_WIDTH = 767;

export type LayoutInfo = {
  width: number;
  height: number;
  isWeb: boolean;
  isCompact: boolean;
  isWide: boolean;
  hasFinePointer: boolean;
  /** Mobile-style on-screen key rows are only useful without a hardware keyboard. */
  showExtraKeys: boolean;
  /** Side-by-side terminal panes need enough horizontal room. */
  splitEnabled: boolean;
};

export function useLayout(): LayoutInfo {
  const { width, height } = useWindowDimensions();
  const [finePointer, setFinePointer] = useState(false);

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia("(pointer: fine)");
    const update = () => setFinePointer(query.matches);
    update();
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", update);
      return () => query.removeEventListener("change", update);
    }
    query.addListener(update);
    return () => query.removeListener(update);
  }, []);

  const isWeb = Platform.OS === "web";
  const isWide = width >= WIDE_MIN_WIDTH;
  const isCompact = width <= COMPACT_MAX_WIDTH;
  const hasFinePointer = isWeb && finePointer;

  return {
    width,
    height,
    isWeb,
    isCompact,
    isWide,
    hasFinePointer,
    showExtraKeys: !(isWide && hasFinePointer),
    splitEnabled: isWide,
  };
}
