import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useAppPreferences } from "./useAppPreferences";

const PREFS_KEY = "noofy.prefs";

describe("useAppPreferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it('defaults to "canvas" when localStorage is empty', () => {
    const { result } = renderHook(() => useAppPreferences());
    expect(result.current.viewMode).toBe("canvas");
  });

  it('returns "classic" when localStorage contains classic', () => {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ viewMode: "classic" }));
    const { result } = renderHook(() => useAppPreferences());
    expect(result.current.viewMode).toBe("classic");
  });

  it('defaults to "canvas" when localStorage contains an unknown value', () => {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ viewMode: "unknown_mode" }));
    const { result } = renderHook(() => useAppPreferences());
    expect(result.current.viewMode).toBe("canvas");
  });

  it('defaults to "canvas" when localStorage contains malformed JSON', () => {
    window.localStorage.setItem(PREFS_KEY, "NOT JSON");
    const { result } = renderHook(() => useAppPreferences());
    expect(result.current.viewMode).toBe("canvas");
  });

  it("setViewMode updates the returned viewMode", () => {
    const { result } = renderHook(() => useAppPreferences());
    act(() => { result.current.setViewMode("classic"); });
    expect(result.current.viewMode).toBe("classic");
  });

  it("setViewMode persists the value to localStorage", () => {
    const { result } = renderHook(() => useAppPreferences());
    act(() => { result.current.setViewMode("classic"); });
    const stored = JSON.parse(window.localStorage.getItem(PREFS_KEY) ?? "{}") as { viewMode: string };
    expect(stored.viewMode).toBe("classic");
  });

  it("setViewMode back to canvas is reflected immediately", () => {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ viewMode: "classic" }));
    const { result } = renderHook(() => useAppPreferences());
    act(() => { result.current.setViewMode("canvas"); });
    expect(result.current.viewMode).toBe("canvas");
  });

  it("keeps multiple hook consumers in sync", () => {
    const first = renderHook(() => useAppPreferences());
    const second = renderHook(() => useAppPreferences());

    act(() => { first.result.current.setViewMode("classic"); });

    expect(first.result.current.viewMode).toBe("classic");
    expect(second.result.current.viewMode).toBe("classic");
  });

  it("updates when the stored preference changes outside the hook", () => {
    const { result } = renderHook(() => useAppPreferences());

    act(() => {
      window.localStorage.setItem(PREFS_KEY, JSON.stringify({ viewMode: "classic" }));
      window.dispatchEvent(new StorageEvent("storage", { key: PREFS_KEY }));
    });

    expect(result.current.viewMode).toBe("classic");
  });
});
