import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { loadThemeSettings, saveThemeSettings } from "@/lib/storage";
import { BUILT_IN_THEMES, DEFAULT_THEME_ID, findTheme, type AppTheme } from "@/lib/themes";

type ThemeContextValue = {
  theme: AppTheme;
  themeId: string;
  themes: AppTheme[];
  ready: boolean;
  setTheme: (id: string) => void;
  saveCustomTheme: (theme: AppTheme) => void;
  deleteCustomTheme: (id: string) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [selectedId, setSelectedId] = useState(DEFAULT_THEME_ID);
  const [custom, setCustom] = useState<AppTheme[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const settings = await loadThemeSettings();
      if (cancelled) return;
      if (settings) {
        setSelectedId(settings.selectedId || DEFAULT_THEME_ID);
        setCustom(settings.custom);
      }
      setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    void saveThemeSettings({ selectedId, custom });
  }, [ready, selectedId, custom]);

  const themes = useMemo(() => [...BUILT_IN_THEMES, ...custom], [custom]);
  const theme = useMemo(
    () => findTheme(themes, selectedId) ?? findTheme(themes, DEFAULT_THEME_ID) ?? themes[0],
    [themes, selectedId],
  );

  const setTheme = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const saveCustomTheme = useCallback((next: AppTheme) => {
    setCustom((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
    setSelectedId(next.id);
  }, []);

  const deleteCustomTheme = useCallback((id: string) => {
    setCustom((previous) => previous.filter((item) => item.id !== id));
    setSelectedId((previous) => (previous === id ? DEFAULT_THEME_ID : previous));
  }, []);

  const value = useMemo(
    () => ({
      theme,
      themeId: theme.id,
      themes,
      ready,
      setTheme,
      saveCustomTheme,
      deleteCustomTheme,
    }),
    [theme, themes, ready, setTheme, saveCustomTheme, deleteCustomTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useAppTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useAppTheme must be used inside ThemeProvider");
  return context;
}
