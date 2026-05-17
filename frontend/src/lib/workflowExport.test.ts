import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  saveWorkflowExportToNativeFile,
  saveWorkflowExportWithFilename,
  validateNoofyExportFilename,
  validateWorkflowExportFilename,
  workflowExportDownloadRequest,
  workflowExportFilename,
} from "./workflowExport";

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
    expect(workflowExportFilename("text2img", ".noofy")).toBe("text2img.noofy");
    expect(workflowExportFilename("unknown__text2img__0.1.0", ".noofy")).toBe("text2img.noofy");
    expect(workflowExportFilename("", ".json")).toBe("workflow.json");
  });

  it("validates and normalizes editable noofy export filenames", () => {
    expect(validateNoofyExportFilename("text2img")).toMatchObject({
      valid: true,
      filename: "text2img.noofy",
    });
    expect(validateNoofyExportFilename("bad/name:noofy")).toMatchObject({
      valid: true,
      filename: "bad-name-noofy.noofy",
    });
    expect(validateNoofyExportFilename("   ")).toMatchObject({
      valid: false,
      message: "Enter a filename.",
    });
  });

  it("validates editable json export filenames", () => {
    expect(validateWorkflowExportFilename("text2img", ".json")).toMatchObject({
      valid: true,
      filename: "text2img.json",
    });
    expect(validateWorkflowExportFilename("bad/name:graph", ".json")).toMatchObject({
      valid: true,
      filename: "bad-name-graph.json",
    });
  });

  it("downloads browser exports with the selected filename", async () => {
    delete window.__TAURI_INTERNALS__;
    const clicked: string[] = [];
    const createObjectUrl = vi.fn(() => "blob:noofy-export");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    fetchMock.mockResolvedValue(new Response(new Uint8Array([110, 111, 111, 102, 121])));

    await expect(saveWorkflowExportWithFilename("/api/workflows/text_to_image_v0/export", "text2img.noofy"))
      .resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/text_to_image_v0/export");
    expect(clicked).toEqual(["text2img.noofy"]);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:noofy-export");
  });

  it("posts current dashboard values when a download request includes input values", async () => {
    delete window.__TAURI_INTERNALS__;
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:json-export") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ workflow: true })));

    await expect(saveWorkflowExportWithFilename(
      workflowExportDownloadRequest(
        "/api/workflows/text_to_image_v0/export/comfyui-json",
        { prompt: "visible prompt" },
      ),
      "text2img.json",
    )).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/text_to_image_v0/export/comfyui-json",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input_values: { prompt: "visible prompt" } }),
      }),
    );
  });
});
