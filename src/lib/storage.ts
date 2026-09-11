import { Platform } from "react-native";

import type { AppTheme } from "@/lib/themes";
import type { Connection, SavedConnection } from "@/lib/types";
import { connectionId } from "@/lib/types";

const KEY = "termx.connections";
const LEGACY_KEY = "termx.connection";
const THEME_KEY = "termx.theme-settings";
const MAX_SAVED = 12;

async function readItem(key: string): Promise<string | null> {
  if (Platform.OS === "web") {
    return globalThis.localStorage?.getItem(key) ?? null;
  }
  const SecureStore = await import("expo-secure-store");
  return SecureStore.getItemAsync(key);
}

async function writeItem(key: string, value: string): Promise<void> {
  if (Platform.OS === "web") {
    globalThis.localStorage?.setItem(key, value);
    return;
  }
  const SecureStore = await import("expo-secure-store");
  await SecureStore.setItemAsync(key, value);
}

async function removeItem(key: string): Promise<void> {
  if (Platform.OS === "web") {
    globalThis.localStorage?.removeItem(key);
    return;
  }
  const SecureStore = await import("expo-secure-store");
  await SecureStore.deleteItemAsync(key);
}

function isConnection(value: unknown): value is Connection {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Connection>;
  return (
    (candidate.protocol === "http" || candidate.protocol === "https") &&
    typeof candidate.host === "string" &&
    candidate.host.length > 0 &&
    typeof candidate.port === "number" &&
    Number.isFinite(candidate.port) &&
    typeof candidate.passcode === "string"
  );
}

function toSaved(value: unknown, savedAt = Date.now()): SavedConnection | null {
  if (!isConnection(value)) return null;
  const stored = (value as Partial<SavedConnection>).savedAt;
  return {
    ...value,
    id: connectionId(value),
    savedAt: typeof stored === "number" ? stored : savedAt,
  };
}

export async function listConnections(): Promise<SavedConnection[]> {
  try {
    const json = await readItem(KEY);
    if (json) {
      const parsed: unknown = JSON.parse(json);
      if (Array.isArray(parsed)) {
        const entries = parsed
          .map((entry) => toSaved(entry))
          .filter((entry): entry is SavedConnection => entry !== null)
          .sort((a, b) => b.savedAt - a.savedAt);
        if (entries.length) return entries;
      }
    }
    const legacy = await readItem(LEGACY_KEY);
    if (legacy) {
      const migrated = toSaved(JSON.parse(legacy));
      if (migrated) {
        await writeItem(KEY, JSON.stringify([migrated]));
        await removeItem(LEGACY_KEY);
        return [migrated];
      }
    }
  } catch {
    /* corrupted or unavailable storage */
  }
  return [];
}

export async function saveConnection(connection: Connection): Promise<SavedConnection> {
  const entry: SavedConnection = {
    ...connection,
    id: connectionId(connection),
    savedAt: Date.now(),
  };
  const existing = await listConnections();
  const next = [entry, ...existing.filter((saved) => saved.id !== entry.id)].slice(0, MAX_SAVED);
  await writeItem(KEY, JSON.stringify(next));
  return entry;
}

export async function forgetConnection(id: string): Promise<void> {
  const existing = await listConnections();
  await writeItem(KEY, JSON.stringify(existing.filter((saved) => saved.id !== id)));
}

export type StoredThemeSettings = {
  selectedId: string;
  custom: AppTheme[];
};

function isAppTheme(value: unknown): value is AppTheme {
  if (!value || typeof value !== "object") return false;
  const theme = value as Partial<AppTheme>;
  return (
    typeof theme.id === "string" &&
    typeof theme.name === "string" &&
    (theme.kind === "dark" || theme.kind === "light") &&
    typeof theme.ui === "object" &&
    theme.ui !== null &&
    typeof theme.terminal === "object" &&
    theme.terminal !== null
  );
}

export async function loadThemeSettings(): Promise<StoredThemeSettings | null> {
  try {
    const json = await readItem(THEME_KEY);
    if (!json) return null;
    const parsed = JSON.parse(json) as Partial<StoredThemeSettings>;
    return {
      selectedId: typeof parsed.selectedId === "string" ? parsed.selectedId : "",
      custom: Array.isArray(parsed.custom) ? parsed.custom.filter(isAppTheme) : [],
    };
  } catch {
    return null;
  }
}

export async function saveThemeSettings(settings: StoredThemeSettings): Promise<void> {
  await writeItem(THEME_KEY, JSON.stringify(settings));
}
