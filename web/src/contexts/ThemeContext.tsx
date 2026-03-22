import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  getSettingsApiV1SettingsGet,
  patchSettingsApiV1SettingsPatch,
} from "@/api/generated/settings/settings";

type ThemeMode = "dark" | "light" | "system";
type ResolvedTheme = "dark" | "light";

interface ThemeContextValue {
  mode: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
}

const STORAGE_KEY = "mixd-theme";
const MEDIA_QUERY = "(prefers-color-scheme: dark)";

const ThemeContext = createContext<ThemeContextValue | null>(null);

function isThemeMode(v: string): v is ThemeMode {
  return v === "dark" || v === "light" || v === "system";
}

function getStoredMode(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && isThemeMode(stored)) return stored;
  return "dark";
}

function resolveTheme(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  if (mode === "system") return systemDark ? "dark" : "light";
  return mode;
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(getStoredMode);
  const [systemDark, setSystemDark] = useState(
    () => typeof window !== "undefined" && matchMedia(MEDIA_QUERY).matches,
  );

  const resolved = resolveTheme(mode, systemDark);

  // Listen for OS preference changes
  useEffect(() => {
    const mql = matchMedia(MEDIA_QUERY);
    const handler = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  // Apply theme to DOM whenever resolved theme changes
  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);

  // Sync from API on mount — if server has a different theme, adopt it
  useEffect(() => {
    let cancelled = false;
    getSettingsApiV1SettingsGet()
      .then((res) => {
        if (cancelled) return;
        const serverMode = res.data.theme_mode;
        if (isThemeMode(serverMode) && serverMode !== getStoredMode()) {
          localStorage.setItem(STORAGE_KEY, serverMode);
          setModeState(serverMode);
        }
      })
      .catch(() => {
        // API unavailable — localStorage value stands
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    // Write to localStorage immediately (instant, flash-free)
    localStorage.setItem(STORAGE_KEY, next);
    setModeState(next);

    // Persist to API in background (durable, cross-device)
    patchSettingsApiV1SettingsPatch({ theme_mode: next }).catch(() => {
      // API unavailable — localStorage persists locally
    });
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, resolvedTheme: resolved, setMode }),
    [mode, resolved, setMode],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within <ThemeProvider>");
  return ctx;
}
