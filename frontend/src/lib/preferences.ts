export const PREFERENCES_STORAGE_KEY = "omega_settings_prefs";
export const PREFERENCES_EVENT = "omega-preferences-change";

export interface LocalPreferences {
  theme: "dark" | "system" | "light";
  density: "comfortable" | "compact";
  reduceMotion: boolean;
  highContrast: boolean;
  largeText: boolean;
  showTechnicalIds: boolean;
  showInternalChannels: boolean;
}

export const DEFAULT_PREFERENCES: LocalPreferences = {
  theme: "dark",
  density: "comfortable",
  reduceMotion: false,
  highContrast: false,
  largeText: false,
  showTechnicalIds: false,
  showInternalChannels: false,
};

export function readPreferences(): LocalPreferences {
  try {
    const storage = typeof window !== "undefined" ? window.localStorage : (typeof globalThis !== "undefined" ? globalThis.localStorage : undefined);
    if (!storage) return DEFAULT_PREFERENCES;
    const stored = storage.getItem(PREFERENCES_STORAGE_KEY);
    return stored
      ? { ...DEFAULT_PREFERENCES, ...(JSON.parse(stored) as Partial<LocalPreferences>) }
      : DEFAULT_PREFERENCES;
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export function applyPreferences(preferences: LocalPreferences) {
  if (typeof document === "undefined" || typeof window === "undefined") return;
  const root = document.documentElement;
  const systemIsLight = window.matchMedia?.("(prefers-color-scheme: light)")?.matches ?? false;
  const light = preferences.theme === "light" || (preferences.theme === "system" && systemIsLight);
  root.dataset.theme = light ? "light" : "dark";
  root.dataset.density = preferences.density;
  root.dataset.reduceMotion = String(preferences.reduceMotion);
  root.dataset.highContrast = String(preferences.highContrast);
  root.dataset.largeText = String(preferences.largeText);
  root.dataset.showTechnicalIds = String(preferences.showTechnicalIds);
}

export function persistPreferences(preferences: LocalPreferences) {
  try {
    const storage = typeof window !== "undefined" ? window.localStorage : (typeof globalThis !== "undefined" ? globalThis.localStorage : undefined);
    storage?.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // Ignore storage errors
  }
  applyPreferences(preferences);
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent(PREFERENCES_EVENT, { detail: preferences }));
  }
}
