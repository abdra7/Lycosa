// Runs before the app renders: the saved theme is applied to <html> so a
// dark-mode user never sees a light first frame (the Flutter app's
// loadInitialThemeMode). Light-first per the brand spec.
import { applyTheme, loadTheme } from "./state/theme-store";

applyTheme(loadTheme());
