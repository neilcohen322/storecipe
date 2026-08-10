import { createContext, type PropsWithChildren, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useColorScheme, View } from "react-native";

import { createThemeStorage, THEME_STORAGE_KEY, type ThemeStorage } from "./storage";
import { getTheme } from "./tokens";
import type { ResolvedScheme, Theme, ThemePreference } from "./types";

type ThemeContextValue = {
  preference: ThemePreference;
  resolvedScheme: ResolvedScheme;
  theme: Theme;
  setPreference: (preference: ThemePreference) => Promise<void>;
};

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function parsePreference(value: string | null | undefined): ThemePreference {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

function resolveScheme(preference: ThemePreference, systemScheme: string | null | undefined): ResolvedScheme {
  return preference === "system" ? (systemScheme === "dark" ? "dark" : "light") : preference;
}

type ThemeProviderProps = PropsWithChildren<{
  storage?: ThemeStorage;
  /** Test-only override; production reads react-native's useColorScheme(). */
  systemSchemeOverride?: "light" | "dark" | null;
}>;

export function ThemeProvider({ children, storage: providedStorage, systemSchemeOverride }: ThemeProviderProps) {
  const defaultStorage = useRef<ThemeStorage | null>(null);
  if (!providedStorage && !defaultStorage.current) defaultStorage.current = createThemeStorage();
  const storage = providedStorage ?? defaultStorage.current!;
  const deviceScheme = useColorScheme();
  const systemScheme = systemSchemeOverride === undefined ? deviceScheme : systemSchemeOverride;
  const [preference, setStoredPreference] = useState<ThemePreference>(() =>
    storage.platform === "web" ? parsePreference(storage.getSync?.(THEME_STORAGE_KEY)) : "system",
  );
  const [hydrated, setHydrated] = useState(storage.platform === "web");

  useEffect(() => {
    if (storage.platform === "web") return;
    let active = true;
    Promise.resolve().then(() => storage.get(THEME_STORAGE_KEY)).then((value) => {
      if (active) {
        setStoredPreference(parsePreference(value));
        setHydrated(true);
      }
    }).catch(() => {
      if (active) setHydrated(true);
    });
    return () => { active = false; };
  }, [storage]);

  const resolvedScheme = resolveScheme(preference, systemScheme);
  const theme = useMemo(() => getTheme(resolvedScheme), [resolvedScheme]);
  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    resolvedScheme,
    theme,
    setPreference: async (nextPreference) => {
      setStoredPreference(nextPreference);
      await storage.set(THEME_STORAGE_KEY, nextPreference);
    },
  }), [preference, resolvedScheme, storage, theme]);

  if (!hydrated) {
    return <View testID="theme-loading" style={{ flex: 1, backgroundColor: getTheme(resolveScheme("system", systemScheme)).colors.canvas }} />;
  }

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used within ThemeProvider");
  return context;
}
