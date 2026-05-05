import { useState } from "react";

export type ViewMode = "canvas" | "classic";

const PREFS_KEY = "noofy.prefs";

function readPrefs(): { viewMode: ViewMode } {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return { viewMode: "canvas" };
    const parsed = JSON.parse(raw) as Partial<{ viewMode: ViewMode }>;
    return { viewMode: parsed.viewMode === "classic" ? "classic" : "canvas" };
  } catch {
    return { viewMode: "canvas" };
  }
}

function writePrefs(prefs: { viewMode: ViewMode }) {
  window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
}

export function useAppPreferences() {
  const [prefs, setPrefs] = useState<{ viewMode: ViewMode }>(readPrefs);

  function setViewMode(mode: ViewMode) {
    const next = { ...prefs, viewMode: mode };
    writePrefs(next);
    setPrefs(next);
  }

  return { viewMode: prefs.viewMode, setViewMode };
}
