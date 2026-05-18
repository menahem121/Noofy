import { useCallback, useSyncExternalStore } from "react";

export type ViewMode = "canvas" | "classic";

const PREFS_KEY = "noofy.prefs";
const PREFS_CHANGED_EVENT = "noofy:prefs-changed";

function storedPrefs(): Record<string, unknown> {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function readViewMode(): ViewMode {
  return storedPrefs().viewMode === "classic" ? "classic" : "canvas";
}

function writePrefs(prefs: Record<string, unknown> & { viewMode: ViewMode }) {
  window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  window.dispatchEvent(new Event(PREFS_CHANGED_EVENT));
}

function subscribe(callback: () => void) {
  function handleStorage(event: StorageEvent) {
    if (event.key === PREFS_KEY || event.key === null) callback();
  }
  window.addEventListener(PREFS_CHANGED_EVENT, callback);
  window.addEventListener("storage", handleStorage);
  return () => {
    window.removeEventListener(PREFS_CHANGED_EVENT, callback);
    window.removeEventListener("storage", handleStorage);
  };
}

export function useAppPreferences() {
  const viewMode = useSyncExternalStore(subscribe, readViewMode, () => "canvas");

  const setViewMode = useCallback((mode: ViewMode) => {
    writePrefs({ ...storedPrefs(), viewMode: mode });
  }, []);

  return { viewMode, setViewMode };
}
