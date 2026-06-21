import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkflowInputDef, WorkflowUserState } from "./api/noofyApi";
import { __resetWorkflowUserStateCacheForTests, useWorkflowUserState } from "./useWorkflowUserState";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyRemoteState(workflowId: string): WorkflowUserState {
  return {
    schema_version: "1",
    workflow_id: workflowId,
    dashboard_version: "",
    values: {},
    layout_overrides: {},
    presentation_overrides: {},
    output_preferences: {},
  };
}

function makeInputIndex(...ids: string[]): Map<string, WorkflowInputDef> {
  const map = new Map<string, WorkflowInputDef>();
  for (const id of ids) {
    map.set(id, {
      id,
      label: id,
      control: "string_field",
      binding: { node_id: "1", input_name: id },
      default: `default-${id}`,
      validation: {},
    });
  }
  return map;
}

describe("useWorkflowUserState", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    __resetWorkflowUserStateCacheForTests();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    fetchMock.mockReset();
  });

  it("does not expose package defaults before the remote state loads", () => {
    fetchMock.mockReturnValue(new Promise(() => {})); // never resolves
    const defaults = { prompt: "a dog" };
    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", defaults, "1.0", makeInputIndex("prompt")),
    );
    expect(result.current.loaded).toBe(false);
    expect(result.current.values).toEqual({});
  });

  it("merges remote values over package defaults after load", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "a cat" },
    };
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));
    const defaults = { prompt: "a dog", seed: 42 };
    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", defaults, "1.0", makeInputIndex("prompt", "seed")),
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("a cat"));
    expect(result.current.loaded).toBe(true);
    // seed not in remote values → default is used
    expect(result.current.values.seed).toBe(42);
  });

  it("falls back to package defaults when the API call fails", async () => {
    fetchMock.mockRejectedValue(new Error("network error"));
    const defaults = { prompt: "fallback" };
    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", defaults, "1.0", makeInputIndex("prompt")),
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("fallback"));
    expect(result.current.loaded).toBe(true);
  });

  it("setValue updates the value immediately and schedules a save", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "original" },
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", makeInputIndex("prompt")),
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("original"));

    act(() => { result.current.setValue("prompt", "updated"); });
    expect(result.current.values.prompt).toBe("updated");

    // Flush debounce timer — should trigger a PUT /user-state
    await act(async () => { vi.advanceTimersByTime(700); });
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeDefined();
    const body = JSON.parse((putCall![1] as RequestInit).body as string) as WorkflowUserState;
    expect(body.values.prompt).toBe("updated");
  });

  it("restoreDefaults calls DELETE /user-state/values and resets to defaults", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "user text" },
    };
    const afterDelete: WorkflowUserState = { ...remote, values: {} };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockResolvedValue(jsonResponse(afterDelete));

    const defaults = { prompt: "creator default" };
    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", defaults, "1.0", makeInputIndex("prompt")),
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("user text"));

    await act(async () => { await result.current.restoreDefaults(); });

    const deleteCall = fetchMock.mock.calls.find(([url, init]) =>
      String(url).includes("/user-state/values") && (init as RequestInit)?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    expect(result.current.values.prompt).toBe("creator default");
  });

  it("setLayoutOverride adds an override and schedules a save", async () => {
    const remote = { ...emptyRemoteState("wf-1"), dashboard_version: "1.0" };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", new Map(), ["ctrl-1"]),
    );
    await waitFor(() => expect(result.current.hasLayoutOverrides).toBe(false));

    await act(async () => {
      await result.current.setLayoutOverride("ctrl-1", { x: 2, y: 3, w: 4, h: 2 });
    });

    expect(result.current.hasLayoutOverrides).toBe(true);
    expect(result.current.layoutOverrides["ctrl-1"]).toMatchObject({ x: 2, y: 3, w: 4, h: 2 });

    await act(async () => { vi.advanceTimersByTime(700); });
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeDefined();
  });

  it("setActionBarPositionOverride saves a personal canvas bar position", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", new Map()),
    );
    await waitFor(() => expect(result.current.actionBarPositionOverride).toBeNull());

    await act(async () => {
      await result.current.setActionBarPositionOverride({ x: 120.4, y: 30.6 });
    });

    expect(result.current.actionBarPositionOverride).toEqual({ x: 120, y: 31 });

    await act(async () => { vi.advanceTimersByTime(700); });
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeDefined();
    const body = JSON.parse((putCall![1] as RequestInit).body as string);
    expect(body.presentation_overrides.action_bar).toEqual({ x: 120, y: 31 });
    expect(body.layout_overrides).toEqual({});
  });

  it("resetLayout calls DELETE /user-state/layout and clears overrides", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      layout_overrides: { "ctrl-1": { x: 0, y: 0, w: 4, h: 2 } },
      presentation_overrides: { action_bar: { x: 30, y: 40 } },
    };
    const afterDelete: WorkflowUserState = { ...remote, layout_overrides: {}, presentation_overrides: {} };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockResolvedValue(jsonResponse(afterDelete));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", new Map(), ["ctrl-1"]),
    );
    await waitFor(() => expect(result.current.hasLayoutOverrides).toBe(true));
    expect(result.current.actionBarPositionOverride).toEqual({ x: 30, y: 40 });

    await act(async () => { await result.current.resetLayout(); });

    const deleteCall = fetchMock.mock.calls.find(([url, init]) =>
      String(url).includes("/user-state/layout") && (init as RequestInit)?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    expect(result.current.hasLayoutOverrides).toBe(false);
    expect(result.current.actionBarPositionOverride).toBeNull();
  });

  it("resets values to creator defaults and prunes stale keys when dashboard_version changes", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "0.9",
      values: { "old-input": "stale", prompt: "kept" },
      layout_overrides: { "old-ctrl": { x: 0, y: 0, w: 4, h: 2 }, "ctrl-1": { x: 1, y: 0, w: 4, h: 2 } },
    };
    // PUT after prune-save
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockResolvedValue(jsonResponse({ ...remote, dashboard_version: "1.0" }));

    const inputIndex = makeInputIndex("prompt");
    // "ctrl-1" is a valid control id; "old-ctrl" is not in inputIndex
    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", { prompt: "default" }, "1.0", inputIndex),
    );
    await waitFor(() => expect(result.current.values.prompt).toBeDefined());

    // old-input should be pruned (not in inputIndex)
    expect("old-input" in result.current.values).toBe(false);
    // Existing values belong to the previous dashboard configuration, so the new creator default is used.
    expect(result.current.values.prompt).toBe("default");
    // old-ctrl override should be pruned (not in inputIndex)
    expect(result.current.hasLayoutOverrides).toBe(false);
  });

  it("keeps valid control layout overrides when dashboard_version changes", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "0.9",
      values: { prompt: "kept" },
      layout_overrides: {
        "old-ctrl": { x: 0, y: 0, w: 4, h: 2 },
        "ctrl-1": { x: 1, y: 0, w: 4, h: 2 },
      },
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockResolvedValue(jsonResponse({ ...remote, dashboard_version: "1.0" }));

    const { result } = renderHook(() =>
      useWorkflowUserState(
        "wf-1",
        { prompt: "default" },
        "1.0",
        makeInputIndex("prompt"),
        ["ctrl-1"],
      ),
    );
    await waitFor(() => expect(result.current.values.prompt).toBeDefined());

    expect(result.current.layoutOverrides["ctrl-1"]).toMatchObject({ x: 1, y: 0, w: 4, h: 2 });
    expect(result.current.layoutOverrides["old-ctrl"]).toBeUndefined();
  });

  it("reloads remote state when package defaults arrive after initial render", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "remote text" },
    };
    fetchMock.mockResolvedValue(jsonResponse(remote));

    const { result, rerender } = renderHook(
      ({ defaults, inputIndex, version }) =>
        useWorkflowUserState("wf-1", defaults, version, inputIndex),
      {
        initialProps: {
          defaults: {},
          inputIndex: new Map<string, WorkflowInputDef>(),
          version: "",
        },
      },
    );
    expect(fetchMock).not.toHaveBeenCalled();

    rerender({
      defaults: { prompt: "creator default" },
      inputIndex: makeInputIndex("prompt"),
      version: "1.0",
    });

    await waitFor(() => expect(result.current.values.prompt).toBe("remote text"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("merges package context changes locally without refetching or reverting edits", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "remote text", removed: "old value" },
      layout_overrides: {
        "keep-layout": { x: 1, y: 0, w: 4, h: 2 },
        "drop-layout": { x: 2, y: 0, w: 4, h: 2 },
      },
      output_preferences: {
        "keep-output": { auto_save: true },
        "drop-output": { auto_save: true },
      },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return Promise.resolve(jsonResponse(remote));
      return Promise.resolve(jsonResponse(remote));
    });

    const { result, rerender } = renderHook(
      ({
        defaults,
        version,
        inputIndex,
        layoutIds,
        outputIds,
      }: {
        defaults: Record<string, unknown>;
        version: string;
        inputIndex: Map<string, WorkflowInputDef>;
        layoutIds: string[];
        outputIds: string[];
      }) => useWorkflowUserState("wf-1", defaults, version, inputIndex, layoutIds, outputIds),
      {
        initialProps: {
          defaults: { prompt: "creator prompt", removed: "creator old" } as Record<string, unknown>,
          version: "1.0",
          inputIndex: makeInputIndex("prompt", "removed"),
          layoutIds: ["keep-layout", "drop-layout"],
          outputIds: ["keep-output", "drop-output"],
        },
      },
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("remote text"));
    expect(result.current.loaded).toBe(true);

    act(() => result.current.setValue("prompt", "edited locally"));
    rerender({
      defaults: { prompt: "new creator prompt", added: "new default" },
      version: "1.1",
      inputIndex: makeInputIndex("prompt", "added"),
      layoutIds: ["keep-layout"],
      outputIds: ["keep-output"],
    });

    expect(result.current.loaded).toBe(true);
    await waitFor(() => {
      expect(result.current.values).toEqual({
        prompt: "edited locally",
        added: "new default",
      });
    });
    expect(result.current.layoutOverrides).toEqual({
      "keep-layout": { x: 1, y: 0, w: 4, h: 2 },
    });
    expect(result.current.outputPreferences).toEqual({
      "keep-output": { auto_save: true },
    });
    expect(fetchMock.mock.calls.filter(([, init]) => !init?.method || init.method === "GET")).toHaveLength(1);

    await act(async () => vi.advanceTimersByTime(700));
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall![1] as RequestInit).body as string).values).toEqual({
      prompt: "edited locally",
      added: "new default",
    });
  });

  it("replaces an empty stale value with a new creator default when the package changes locally", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { image: null, prompt: "kept prompt" },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return Promise.resolve(jsonResponse(remote));
      return Promise.resolve(jsonResponse(remote));
    });
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
    };
    const inputIndex = makeInputIndex("image", "prompt");

    const { result, rerender } = renderHook(
      ({ defaults, version }: { defaults: Record<string, unknown>; version: string }) =>
        useWorkflowUserState("wf-1", defaults, version, inputIndex),
      {
        initialProps: {
          defaults: { image: null, prompt: "old prompt" } as Record<string, unknown>,
          version: "1.0",
        },
      },
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("kept prompt"));
    expect(result.current.values.image).toBeNull();

    rerender({
      defaults: { image: packagedDefault, prompt: "new prompt" },
      version: "1.1",
    });

    await waitFor(() => {
      expect(result.current.values).toEqual({
        image: packagedDefault,
        prompt: "kept prompt",
      });
    });

    await act(async () => vi.advanceTimersByTime(700));
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(JSON.parse((putCall![1] as RequestInit).body as string).values).toEqual({
      image: packagedDefault,
      prompt: "kept prompt",
    });
  });

  it("flushes a pending debounced save when the hook unmounts", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "remote text" },
    };
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));

    const { result, unmount } = renderHook(() =>
      useWorkflowUserState("wf-1", { prompt: "creator prompt" }, "1.0", makeInputIndex("prompt")),
    );
    await waitFor(() => expect(result.current.values.prompt).toBe("remote text"));

    act(() => result.current.setValue("prompt", "edited before leaving"));
    unmount();

    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall![1] as RequestInit).body as string).values.prompt)
      .toBe("edited before leaving");
  });

  it("setValue does not clobber existing layout overrides", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
      values: { prompt: "original" },
      layout_overrides: { "ctrl-1": { x: 2, y: 0, w: 4, h: 2 } },
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockResolvedValue(jsonResponse(remote));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", makeInputIndex("prompt"), ["ctrl-1"]),
    );
    await waitFor(() => expect(result.current.hasLayoutOverrides).toBe(true));

    act(() => { result.current.setValue("prompt", "changed"); });

    await act(async () => { vi.advanceTimersByTime(700); });
    const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "PUT");
    const body = JSON.parse((putCall![1] as RequestInit).body as string) as WorkflowUserState;
    // layout_overrides must still be present in the PUT payload
    expect(body.layout_overrides["ctrl-1"]).toBeDefined();
  });

  it("setOutputPreference updates latest snapshot and schedules a save", async () => {
    const remote: WorkflowUserState = {
      ...emptyRemoteState("wf-1"),
      dashboard_version: "1.0",
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(remote));
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(remote)));

    const { result } = renderHook(() =>
      useWorkflowUserState("wf-1", {}, "1.0", new Map(), ["result"]),
    );
    await waitFor(() => expect(result.current.outputPreferences).toEqual({}));

    act(() => {
      result.current.setOutputPreference("result", { auto_save: true });
    });

    expect(result.current.getOutputPreferencesSnapshot()).toEqual({ result: { auto_save: true } });

    act(() => {
      vi.advanceTimersByTime(700);
    });

    const putCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).includes("/user-state") && (init as RequestInit)?.method === "PUT",
    );
    expect(putCall).toBeTruthy();
    const body = JSON.parse((putCall![1] as RequestInit).body as string) as WorkflowUserState;
    expect(body.output_preferences.result.auto_save).toBe(true);
  });
});
