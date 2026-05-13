import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveWorkflowExportToNativeFile, workflowExportFilename } from "./workflowExport";

const invokeMock = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({
  invoke: invokeMock,
}));

describe("workflowExport", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.__TAURI_INTERNALS__ = {};
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
    vi.unstubAllGlobals();
    invokeMock.mockReset();
    fetchMock.mockReset();
  });

  it("opens a native save picker, downloads from the backend, and writes the selected file", async () => {
    invokeMock.mockImplementation((command: string, args: Record<string, unknown>) => {
      if (command === "select_save_file") {
        expect(args).toEqual({ defaultFilename: "Text to Image.noofy" });
        return Promise.resolve("/Users/test/Desktop/Text to Image.noofy");
      }
      if (command === "save_binary_file") {
        expect(args).toEqual({
          path: "/Users/test/Desktop/Text to Image.noofy",
          bytes: [110, 111, 111, 102, 121],
        });
        return Promise.resolve("/Users/test/Desktop/Text to Image.noofy");
      }
      throw new Error(`Unexpected command ${command}`);
    });
    fetchMock.mockResolvedValue(new Response(new Uint8Array([110, 111, 111, 102, 121])));

    await expect(saveWorkflowExportToNativeFile("/api/workflows/text_to_image_v0/export", "Text to Image.noofy"))
      .resolves.toBe("/Users/test/Desktop/Text to Image.noofy");

    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/text_to_image_v0/export");
  });

  it("does not call the backend when the save picker is cancelled", async () => {
    invokeMock.mockResolvedValue(null);

    await expect(saveWorkflowExportToNativeFile("/api/workflows/text_to_image_v0/export", "Text to Image.noofy"))
      .resolves.toBeNull();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("builds filesystem-safe export filenames", () => {
    expect(workflowExportFilename("Portrait/Restore: v1.noofy", ".noofy")).toBe("Portrait-Restore- v1.noofy");
    expect(workflowExportFilename("", ".json")).toBe("workflow.json");
  });
});
