import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useModelInventory } from "./useModelInventory";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const inventory = {
  summary: {
    total_count: 1,
    noofy_count: 1,
    external_comfyui_count: 0,
    missing_count: 0,
    total_known_size_bytes: 1,
  },
  folders: {
    noofy_models_dir: "/tmp/Noofy Models",
    external_comfyui_models_dir: null,
    categories: [],
  },
  models: [],
  tags: [],
};

describe("useModelInventory", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("keeps the known inventory when a silent refresh fails", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(inventory));
    const { result } = renderHook(() => useModelInventory());

    await act(async () => {
      await result.current.refreshInventory();
    });
    expect(result.current.inventoryState.inventory).toEqual(inventory);

    fetchMock.mockRejectedValueOnce(new Error("temporary inventory failure"));
    await act(async () => {
      await result.current.refreshInventory({ silent: true });
    });

    await waitFor(() => expect(result.current.inventoryState.error).toBe("temporary inventory failure"));
    expect(result.current.inventoryState.inventory).toEqual(inventory);
  });
});
