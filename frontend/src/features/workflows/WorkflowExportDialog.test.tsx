import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowExportDialog } from "./WorkflowExportDialog";

// The dialog loads custom workflow icons in an effect on mount. Tests that do not
// otherwise await an async result must flush that pending fetch so its state update
// is wrapped in act(...).
async function flushPendingEffects() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("WorkflowExportDialog", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/workflow-icons")) {
        return Promise.resolve(new Response(JSON.stringify({ icons: [] }), {
          headers: { "Content-Type": "application/json" },
        }));
      }
      return Promise.resolve(new Response(new Uint8Array([110, 111, 111, 102, 121])));
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:noofy-export") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    fetchMock.mockReset();
  });

  it("shows editable noofy metadata and read-only package details", async () => {
    render(
      <WorkflowExportDialog
        workflowName="Cleanup Flow"
        exportUrl="/api/workflows/cleanup/export"
        extension=".noofy"
        review={{
          name: "Cleanup Flow",
          description: "Clean up images.",
          author: "Noofy User",
          website: "https://example.test",
          category: "Inpainting",
          tags: ["cleanup", "portrait"],
          icon: "image",
          source: "Imported",
          requiredModels: [{ name: "cleanup.safetensors", type: "Checkpoint", status_label: "Available" }],
        }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toHaveValue("Cleanup Flow");
    expect(screen.getByLabelText("Description")).toHaveValue("Clean up images.");
    expect(screen.getByLabelText("Author")).toHaveValue("Noofy User");
    expect(screen.getByLabelText("Website")).toHaveValue("https://example.test");
    expect(screen.getByLabelText("Category")).toHaveValue("Inpainting");
    expect(screen.getByLabelText("Tags")).toHaveValue("cleanup, portrait");
    expect(screen.getByRole("radiogroup", { name: "Workflow icon" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import icon" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Image" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByLabelText("Icon")).not.toBeInTheDocument();
    expect(screen.getByText("Imported")).toBeInTheDocument();
    expect(screen.getByText("cleanup.safetensors")).toBeInTheDocument();

    await flushPendingEffects();
  });

  it("cancels without exporting", async () => {
    const onClose = vi.fn();
    render(
      <WorkflowExportDialog
        workflowName="Cleanup Flow"
        exportUrl="/api/workflows/cleanup/export"
        extension=".noofy"
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);

    await flushPendingEffects();
  });

  it("exports edited noofy metadata in the export payload", async () => {
    render(
      <WorkflowExportDialog
        workflowName="Cleanup Flow"
        exportUrl="/api/workflows/cleanup/export"
        extension=".noofy"
        inputValues={{ prompt: "local prompt should stay local" }}
        review={{ name: "Cleanup Flow", description: "Clean up images." }}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Workflow name"), { target: { value: "Reviewed Cleanup" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Export-ready package." } });
    fireEvent.change(screen.getByLabelText("Author"), { target: { value: "Noofy User" } });
    fireEvent.change(screen.getByLabelText("Website"), { target: { value: "https://example.test" } });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "Restoration" } });
    fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "cleanup, portrait" } });
    fireEvent.click(screen.getByRole("radio", { name: "Sparkles" }));
    fireEvent.click(screen.getByRole("button", { name: "Export .noofy" }));

    let exportCall: unknown[] | undefined;
    await waitFor(() => {
      exportCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
      expect(exportCall).toBeTruthy();
    });
    const [, init] = exportCall as [RequestInfo | URL, RequestInit | undefined];
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({
      export_metadata: {
        name: "Reviewed Cleanup",
        description: "Export-ready package.",
        author: "Noofy User",
        website: "https://example.test",
        category: "Restoration",
        tags: ["cleanup", "portrait"],
        icon: "sparkles",
      },
    });
    expect(JSON.parse(String((init as RequestInit).body))).not.toHaveProperty("input_values");
  });

  it("imports and deletes custom icons", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflow-icons") && init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({
          id: "asset:custom-icon.png",
          asset_id: "custom-icon.png",
          label: "custom.png",
          kind: "custom",
          url: "/api/assets/custom-icon.png",
        }), { headers: { "Content-Type": "application/json" } }));
      }
      if (url.endsWith("/api/workflow-icons/asset%3Acustom-icon.png") && init?.method === "DELETE") {
        return Promise.resolve(new Response(JSON.stringify({ deleted: true, id: "asset:custom-icon.png" })));
      }
      if (url.endsWith("/api/workflow-icons")) {
        return Promise.resolve(new Response(JSON.stringify({ icons: [] }), {
          headers: { "Content-Type": "application/json" },
        }));
      }
      return Promise.resolve(new Response(new Uint8Array([110, 111, 111, 102, 121])));
    });
    render(
      <WorkflowExportDialog
        workflowName="Cleanup Flow"
        exportUrl="/api/workflows/cleanup/export"
        extension=".noofy"
        onClose={vi.fn()}
      />,
    );

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["png"], "custom.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByRole("radio", { name: "custom.png" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("button", { name: "Delete custom.png" }));

    await waitFor(() => {
      expect(screen.queryByRole("radio", { name: "custom.png" })).not.toBeInTheDocument();
    });
  });
});
