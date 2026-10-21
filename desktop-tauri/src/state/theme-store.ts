export type ThemeMode = "light" | "dark";

const KEY = "lycosa_theme_mode";

/** The saved preference; light when nothing (or nothing readable) is saved. */
export function loadTheme(): ThemeMode {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function saveTheme(mode: ThemeMode): void {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    // a blocked storage only costs the remembered choice
  }
}

export function applyTheme(mode: ThemeMode): void {
  document.documentElement.dataset.theme = mode;
}
