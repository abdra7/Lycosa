import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { applyTheme, loadTheme, saveTheme, type ThemeMode } from "./theme-store";

interface ThemeApi {
  mode: ThemeMode;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeApi>({ mode: "light", toggle: () => {} });

/** Light/dark preference: starts from the value applied before the first
 * frame (theme-boot.ts) and persists every change. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => loadTheme());
  const toggle = useCallback(() => {
    setMode((current) => {
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      saveTheme(next);
      return next;
    });
  }, []);
  const api = useMemo(() => ({ mode, toggle }), [mode, toggle]);
  return <ThemeContext.Provider value={api}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
