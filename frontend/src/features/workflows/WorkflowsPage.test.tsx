import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "../app/AppLayout";
import { RuntimeStatusProvider } from "../app/RuntimeStatusProvider";
import { WorkflowLibraryProvider } from "../home/WorkflowLibraryProvider";
import { WorkflowsPage } from "./WorkflowsPage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const workflows = [
  {
    id: "native_text",
    name: "Native Text",
    version: "1.0.0",
    description: "Generate an image from a prompt.",
    icon: "sparkles",
    source_label: "Native Noofy",
    main_model: { name: "SDXL Base", type: "checkpoint", size_bytes: 1 },
    category: "Txt2img",
    last_opened: null,
    tags: ["starter"],
    missing_model_count: 0,
    needs_setup: false,
    can_remove: false,
    can_export_noofy: false,
    can_export_comfyui_json: true,
    status: "installed",
    status_label: "Installed",
  },
  {
    id: "imported_cleanup",
    name: "Cleanup Flow",
    version: "1.0.0",
    description: "Clean up images.",
    icon: "image",
    source_label: "Imported",
    main_model: { name: "cleanup.safetensors", type: "checkpoint", size_bytes: 2 },
    category: "Inpainting",
    last_opened: "2026-05-10T12:00:00Z",
    tags: ["cleanup"],
    missing_model_count: 1,
    needs_setup: false,
    can_remove: true,
    can_export_noofy: true,
    can_export_comfyui_json: true,
    status: "imported",
    status_label: "Imported",
  },
];

const details = {
  ...workflows[1],
  overview: {
    description: "Clean up images.",
    author: "Artist",
    website: "https://example.test",
    source: "Imported",
    version: "1.0.0",
  },
  models_used: [
    {
      name: "cleanup.safetensors",
      type: "checkpoint",
      size_bytes: 2,
      status: "missing",
      status_label: "Missing",
      folder: "checkpoints",
      source_path: null,
    },
  ],
  run_history: {
    last_run_status: null,
    last_started_at: null,
    last_finished_at: null,
    last_duration_seconds: null,
    average_duration_seconds: null,
    last_error: null,
    run_count: 0,
  },
  organization: {
    category: "Inpainting",
    tags: ["cleanup"],
    icon: "image",
  },
  advanced: {
    package_id: "cleanup",
    engine: "comfyui",
    trust_level: "quarantined_community",
    trust_label: "Community",
    can_export_noofy: true,
    can_export_comfyui_json: true,
    can_remove: true,
  },
};

const workflowPackage = {
  metadata: { id: "imported_cleanup", name: "Cleanup Flow", version: "1.0.0", description: "Clean up images." },
  inputs: [
    {
      id: "prompt",
      label: "Prompt",
      control: "textarea",
      binding: { node_id: "1", input_name: "text" },
      default: "",
      validation: {},
    },
  ],
  outputs: [
    { id: "image", label: "Image", node_id: "9", type: "image" },
  ],
  dashboard: {
    version: "0.1.0",
    status: "configured",
    sections: [
      {
        id: "main",
        title: "Main",
        controls: [
          { id: "prompt_widget", type: "textarea", label: "Prompt", input_id: "prompt", layout: { x: 0, y: 0, w: 12, h: 4 } },
          { id: "image_widget", type: "result_image", label: "Image", output_id: "image", layout: { x: 12, y: 0, w: 12, h: 8 } },
        ],
      },
    ],
  },
};

describe("WorkflowsPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();
  const onOpenWorkflow = vi.fn();
  const onEditWidgets = vi.fn();
  const onEditDashboard = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse({
          mode: "managed",
          reachable: true,
          base_url: "http://127.0.0.1:8188",
          repo_dir: "",
          managed_process_running: true,
          sidecar_starting: false,
          pid: 1,
          error: null,
          environment: null,
          crash_count: 0,
          restart_attempt: 0,
          max_restart_attempts: 3,
          uptime_seconds: 1,
          last_crash_at: null,
        }));
      }
      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse({ cpu: null, memory: null, vram: null }));
      }
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(jsonResponse(workflows));
      }
      if (url.endsWith("/api/workflows/imported_cleanup/details")) {
        return Promise.resolve(jsonResponse(details));
      }
      if (url.endsWith("/api/workflows/imported_cleanup/package")) {
        return Promise.resolve(jsonResponse(workflowPackage));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function renderPage() {
    return render(
      <RuntimeStatusProvider>
        <WorkflowLibraryProvider>
          <SidebarProvider>
            <WorkflowsPage
              onNavigate={onNavigate}
              onOpenWorkflow={onOpenWorkflow}
              onEditWidgets={onEditWidgets}
              onEditDashboard={onEditDashboard}
            />
          </SidebarProvider>
        </WorkflowLibraryProvider>
      </RuntimeStatusProvider>,
    );
  }

  it("renders list data from the lightweight workflows endpoint and filters rows", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();
    expect(screen.getByText("Native Text")).toBeInTheDocument();
    expect(screen.getByText("Cleanup Flow")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search workflows..."), { target: { value: "cleanup" } });

    expect(screen.queryByText("Native Text")).not.toBeInTheDocument();
    expect(screen.getByText("Cleanup Flow")).toBeInTheDocument();
  });

  it("loads details lazily when a row is clicked", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/imported_cleanup/details", expect.anything());
    });
    expect(await screen.findByText("Models used")).toBeInTheDocument();
    expect(screen.getAllByText("cleanup.safetensors").length).toBeGreaterThan(0);
  });

  it("omits remove workflow for native workflows and routes row actions", async () => {
    renderPage();

    await screen.findByText("Native Text");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));

    expect(screen.queryByRole("menuitem", { name: /remove workflow/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /export as .noofy/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("menuitem", { name: "Open" })[0]);
    expect(onOpenWorkflow).toHaveBeenCalledWith("native_text");
  });

  it("loads the workflow package before opening dashboard editing", async () => {
    renderPage();

    await screen.findByText("Cleanup Flow");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Cleanup Flow" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit dashboard" }));

    await waitFor(() => {
      expect(onEditDashboard).toHaveBeenCalledWith(
        expect.objectContaining({
          workflowId: "imported_cleanup",
          workflowName: "Cleanup Flow",
        }),
      );
    });
    expect(onEditDashboard.mock.calls[0][0].widgets).toHaveLength(2);
  });
});
