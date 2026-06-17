import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const runtime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  sidecar_starting: false,
  pid: 123,
  error: null,
  environment: { prepared: true },
  crash_count: 0,
  restart_attempt: 0,
  max_restart_attempts: 3,
  uptime_seconds: 1,
  last_crash_at: null,
};

const workflowSummary = {
  id: "text_to_image_v0",
  name: "Text to Image",
  version: "0.1.0",
  description: "Generate new images from a simple text prompt.",
  source_label: "Native Noofy",
  main_model: { name: "SDXL Base", type: "checkpoint", size_bytes: 1 },
  category: "Txt2img",
  last_opened: null,
  tags: ["starter"],
  missing_model_count: 0,
  needs_setup: false,
  can_remove: false,
  can_export_noofy: true,
  can_export_comfyui_json: true,
  status: "installed",
  status_label: "Installed",
};

const workflowStatus = {
  workflow_id: "text_to_image_v0",
  workflow: workflowSummary,
  install: {},
  required_actions: [],
  compatibility_guidance: [],
  runner: null,
  runner_status: "not_started",
  can_prepare: true,
  can_cancel_preparation: false,
  can_cancel_job: false,
};

const packageData = {
  metadata: {
    id: "text_to_image_v0",
    name: "Text to Image",
    version: "0.1.0",
    description: "Generate a new image from a prompt.",
  },
  inputs: [
    {
      id: "prompt",
      label: "Prompt",
      control: "textarea",
      binding: { node_id: "6", input_name: "text" },
      default: "a lake",
      validation: {},
    },
  ],
  outputs: [{ id: "image", label: "Image", node_id: "9", type: "image" }],
  dashboard: {
    version: "0.1.0",
    status: "configured",
    sections: [
      {
        id: "main",
        title: "Main",
        controls: [
          { id: "prompt", type: "textarea", label: "Prompt", input_id: "prompt", layout: { x: 0, y: 0, w: 16, h: 6 } },
        ],
      },
    ],
  },
};

const modelSummary = {
  workflow_id: "text_to_image_v0",
  total_count: 0,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 0,
  needs_manual_download_count: 0,
  ready_to_run: true,
  models: [],
};

describe("App workflow tabs", () => {
  const fetchMock = vi.fn();
  let lastOpened: string | null = null;
  let activeRunCount = 0;
  let failedCancelCount = 0;
  let leaseAvailable = false;
  let jobProgressStatus = "running";
  let workflowListSummary: typeof workflowSummary & Record<string, unknown> = workflowSummary;
  let importedWorkflowSummaries: Array<typeof workflowSummary & Record<string, unknown>> = [];
  let importPreviewResponses: Map<string, Record<string, unknown>> = new Map();

  beforeEach(() => {
    lastOpened = null;
    activeRunCount = 0;
    failedCancelCount = 0;
    leaseAvailable = false;
    jobProgressStatus = "running";
    workflowListSummary = workflowSummary;
    importedWorkflowSummaries = [];
    importPreviewResponses = new Map();
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(runtime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse({ cpu: null, ram: null, vram: null }));
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(jsonResponse([
          { ...workflowListSummary, last_opened: lastOpened },
          ...importedWorkflowSummaries,
        ]));
      }
      if (url.includes("/api/workflows/import/preview") && method === "POST") {
        const filename = queryParam(url, "filename");
        const response = filename ? importPreviewResponses.get(filename) : null;
        if (response) {
          if (!response.import_session_id && response.workflow && typeof response.workflow === "object") {
            upsertImportedWorkflow(response.workflow as typeof workflowSummary & Record<string, unknown>);
          }
          return Promise.resolve(jsonResponse(response));
        }
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/open") && method === "POST") {
        lastOpened = "2026-05-29T12:00:00+00:00";
        return Promise.resolve(jsonResponse({
          workflow_id: "text_to_image_v0",
          last_opened: lastOpened,
          workflow: {
            ...workflowSummary,
            last_opened: lastOpened,
          },
        }));
      }
      if (url.endsWith("/api/settings/apis")) {
        return Promise.resolve(jsonResponse({ providers: {}, credential_store: { available: true } }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/bindable-inputs")) {
        return Promise.resolve(jsonResponse({
          workflow_id: "text_to_image_v0",
          enrichment: "heuristic",
          nodes: [
            {
              node_id: "6",
              node_type: "CLIPTextEncode",
              is_image_node: false,
              is_lora_node: false,
              inputs: [
                {
                  input_name: "text",
                  current_value: "a lake",
                  kind: "string",
                  suggested_widget_type: "textarea",
                  widget_types: ["textarea", "string_field"],
                },
              ],
            },
          ],
        }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(packageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(modelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", valid: true, missing_models: [], errors: [] }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
          output_preferences: {},
        }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/runner/leases")) {
        if (leaseAvailable) {
          return Promise.resolve(jsonResponse({
            workflow_id: "text_to_image_v0",
            status: "idle_warm",
            lease_id: "lease-1",
            runner: { runner_id: "isolated-1", open_workflow_lease_count: 1 },
          }));
        }
        return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", status: "no_runner", lease_id: null, runner: null }));
      }
      if (url.includes("/runner/leases/") && method === "DELETE") {
        return Promise.resolve(jsonResponse({
          workflow_id: "text_to_image_v0",
          status: "idle",
          lease_id: "lease-1",
          runner: { runner_id: "isolated-1", open_workflow_lease_count: 0 },
        }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        activeRunCount = 1;
        return Promise.resolve(jsonResponse({ job_id: "job-1", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/runs/active-and-queued")) {
        const active = url.includes("text_to_image_v0") ? activeRunCount : 0;
        return Promise.resolve(jsonResponse({ active_count: active, queued_count: 0, total_count: active }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") && method === "POST") {
        activeRunCount = 0;
        return Promise.resolve(jsonResponse({
          canceled_active_count: 1,
          canceled_queued_count: 0,
          already_terminal_count: 0,
          failed_to_cancel_count: failedCancelCount,
        }));
      }
      if (url.endsWith("/api/jobs/job-1/progress")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-1",
          status: jobProgressStatus,
          value: jobProgressStatus === "running" ? 2 : null,
          max: jobProgressStatus === "running" ? 10 : null,
          current_node: null,
          message: jobProgressStatus === "running" ? "Generating..." : null,
        }));
      }
      if (url.endsWith("/api/jobs/job-1/cancel") && method === "POST") {
        return Promise.resolve(jsonResponse({ job_id: "job-1", status: "canceled", value: null, max: null, current_node: null, message: "Canceled." }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  function queryParam(url: string, key: string) {
    return new URL(url, "http://localhost").searchParams.get(key);
  }

  function upsertImportedWorkflow(workflow: typeof workflowSummary & Record<string, unknown>) {
    importedWorkflowSummaries = [
      workflow,
      ...importedWorkflowSummaries.filter((item) => item.id !== workflow.id),
    ];
  }

  function importedWorkflow(id: string, name: string, overrides: Record<string, unknown> = {}) {
    return {
      ...workflowSummary,
      id,
      name,
      description: "",
      source_label: "Imported",
      trust_level: "quarantined_community",
      status: "imported",
      status_label: "Imported",
      can_remove: true,
      ...overrides,
    };
  }

  function importResponse(workflow: ReturnType<typeof importedWorkflow>, overrides: Record<string, unknown> = {}) {
    return {
      import_session_id: null,
      workflow_id: workflow.id,
      status: workflow.status,
      user_facing_message: "Workflow imported",
      workflow,
      required_model_count: 0,
      custom_node_count: 0,
      unresolved_input_count: 0,
      duplicate_identity: null,
      model_summary: null,
      ...overrides,
    };
  }

  function missingModel(filename: string) {
    return {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename,
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: ["https://example.test/model.safetensors"],
      source_availability: "known",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      reference_count: 1,
      references: [],
      dedup_uncertain: false,
      message: "Noofy can try to resolve and download this model before the workflow runs.",
    };
  }

  function fileDataTransfer(files: File[]) {
    return {
      types: ["Files"],
      files,
      items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
      dropEffect: "",
      effectAllowed: "",
    };
  }

  function internalWidgetDragDataTransfer() {
    return {
      types: ["text/plain"],
      files: [],
      items: [],
      effectAllowed: "",
      dropEffect: "",
      setData: vi.fn(),
      getData: vi.fn(),
      clearData: vi.fn(),
    };
  }

  function dropFiles(files: File[]) {
    const dataTransfer = fileDataTransfer(files);
    fireEvent.dragEnter(window, { dataTransfer });
    fireEvent.dragOver(window, { dataTransfer });
    fireEvent.drop(window, { dataTransfer });
    return dataTransfer;
  }

  function importPreviewWasRequested(filename?: string) {
    return fetchMock.mock.calls.some(([url]) => {
      const value = String(url);
      if (!value.includes("/api/workflows/import/preview")) return false;
      return filename ? queryParam(value, "filename") === filename : true;
    });
  }

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    window.sessionStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("opens a workflow tab, avoids duplicates, and closes active tabs back to Home", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) =>
        String(url).endsWith("/api/workflows/text_to_image_v0/open") && (init as RequestInit | undefined)?.method === "POST",
      )).toBe(true);
    });
    const tab = await screen.findByRole("button", { name: "Text to Image" });
    expect(tab).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("button", { name: "Close Text to Image workspace tab" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Go to home" }));
    const recentSection = await screen.findByRole("region", { name: "Recently Opened" });
    expect(within(recentSection).getByRole("heading", { name: "Text to Image" })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    expect(screen.getAllByRole("button", { name: "Close Text to Image workspace tab" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Close Text to Image workspace tab" })).not.toBeInTheDocument();
    });
    expect(await screen.findByText("Built-in Workflows")).toBeInTheDocument();
  });

  it("routes workflows that need input setup to the dashboard builder instead of opening the run view", async () => {
    workflowListSummary = {
      ...workflowSummary,
      status: "needs_input_setup",
      status_label: "Needs input setup",
      needs_setup: true,
      dashboard_status: "not_configured",
      dashboard_ready: false,
    };

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Workflows" }));
    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();
    await screen.findByText("Text to Image");
    fireEvent.click(await screen.findByRole("button", { name: "Open" }));

    expect(await screen.findByRole("heading", { name: /Dashboard Builder/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close Text to Image workspace tab" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/workflows/text_to_image_v0/open"))).toBe(false);
    expect(JSON.parse(window.localStorage.getItem("noofy.appRoute.v1") ?? "{}")).toMatchObject({ name: "workflows" });
  });

  it("restores tabs as shortcuts only and does not open leases until the tab is viewed", async () => {
    window.localStorage.setItem(
      "noofy.workflowTabs.v1",
      JSON.stringify([{ workflowId: "text_to_image_v0", workflowName: "Text to Image", lastActivatedAt: 1 }]),
    );

    render(<App />);

    expect(await screen.findByRole("button", { name: "Text to Image" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/runner/leases"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Text to Image" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/workflows/text_to_image_v0/runner/leases"))).toBe(true);
    });
  });

  it("persists and restores the last safe app route", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Workflows" }));
    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem("noofy.appRoute.v1") ?? "{}")).toMatchObject({
        name: "workflows",
      });
    });

    cleanup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();
  });

  it("starts the staged .noofy import flow when a workflow package is dropped on Home", async () => {
    const workflow = importedWorkflow("home_drop", "Home Drop");
    importPreviewResponses.set("home-drop.noofy", importResponse(workflow));

    render(<App />);

    expect(await screen.findByText("Built-in Workflows")).toBeInTheDocument();
    dropFiles([new File(["archive"], "home-drop.noofy", { type: "application/octet-stream" })]);

    await waitFor(() => expect(importPreviewWasRequested("home-drop.noofy")).toBe(true));
    expect(await screen.findByText("Home Drop was added to your local workflows.")).toBeInTheDocument();
  });

  it("advertises .noofy and raw .json support in the global drop overlay", async () => {
    render(<App />);

    expect(await screen.findByText("Built-in Workflows")).toBeInTheDocument();
    const dataTransfer = fileDataTransfer([new File(["archive"], "overlay.noofy", { type: "application/octet-stream" })]);
    fireEvent.dragEnter(window, { dataTransfer });

    expect(screen.getByText("Drop workflow package to import")).toBeInTheDocument();
    expect(screen.getByText(".noofy files use the normal Noofy import review.")).toBeInTheDocument();
    expect(screen.getByText(".json")).toBeInTheDocument();

    fireEvent.dragLeave(window, { dataTransfer });
  });

  it("starts the staged .noofy import flow when a workflow package is dropped on a workflow run page", async () => {
    const workflow = importedWorkflow("run_drop", "Run Drop");
    importPreviewResponses.set("run-drop.noofy", importResponse(workflow));

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    expect(await screen.findByRole("button", { name: "Run workflow" })).toBeInTheDocument();

    dropFiles([new File(["archive"], "run-drop.noofy", { type: "application/octet-stream" })]);

    await waitFor(() => expect(importPreviewWasRequested("run-drop.noofy")).toBe(true));
    expect(await screen.findByText("Run Drop was added to your local workflows.")).toBeInTheDocument();
  });

  it("shows the duplicate workflow modal for a dropped duplicate .noofy package", async () => {
    const workflow = importedWorkflow("duplicate_drop", "Duplicate Drop");
    importPreviewResponses.set(
      "duplicate-drop.noofy",
      importResponse(workflow, {
        import_session_id: "duplicate-session",
        status: "duplicate_identity",
        duplicate_identity: {
          existing_workflow: workflowSummary,
          user_facing_message: "Noofy already has Text to Image.",
        },
      }),
    );

    render(<App />);

    await screen.findByText("Built-in Workflows");
    dropFiles([new File(["archive"], "duplicate-drop.noofy", { type: "application/octet-stream" })]);

    expect(await screen.findByRole("dialog", { name: "Duplicate Drop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replace Existing Workflow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import as Copy" })).toBeInTheDocument();
  });

  it("shows the required-model modal for a dropped .noofy package with missing models", async () => {
    const workflow = importedWorkflow("missing_model_drop", "Missing Model Drop");
    const model = missingModel("missing-drop.safetensors");
    importPreviewResponses.set(
      "missing-model-drop.noofy",
      importResponse(workflow, {
        import_session_id: "missing-model-session",
        status: "missing_models",
        required_model_count: 1,
        model_summary: {
          workflow_id: workflow.id,
          total_count: 1,
          available_count: 0,
          possible_match_count: 0,
          missing_count: 1,
          needs_manual_download_count: 0,
          ready_to_run: false,
          models: [model],
        },
      }),
    );

    render(<App />);

    await screen.findByText("Built-in Workflows");
    dropFiles([new File(["archive"], "missing-model-drop.noofy", { type: "application/octet-stream" })]);

    expect(await screen.findByRole("dialog", { name: "Missing Model Drop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Missing Models" })).toBeInTheDocument();
    expect(screen.getByText("missing-drop.safetensors")).toBeInTheDocument();
  });

  it("rejects unsupported dropped files without starting workflow import", async () => {
    render(<App />);

    await screen.findByText("Built-in Workflows");
    dropFiles([new File(["image"], "avatar.png", { type: "image/png" })]);

    expect(await screen.findByText(/avatar\.png is not a supported workflow import file/i)).toBeInTheDocument();
    expect(importPreviewWasRequested()).toBe(false);
  });

  it("ignores unsupported files dropped on an explicitly local upload target", async () => {
    render(<App />);

    await screen.findByText("Built-in Workflows");
    const uploadTarget = document.createElement("div");
    uploadTarget.setAttribute("data-noofy-workflow-import-drop-ignore", "");
    document.body.appendChild(uploadTarget);
    try {
      const dataTransfer = fileDataTransfer([new File(["image"], "local-upload.png", { type: "image/png" })]);
      fireEvent.dragEnter(uploadTarget, { dataTransfer });
      fireEvent.dragOver(uploadTarget, { dataTransfer });
      fireEvent.drop(uploadTarget, { dataTransfer });

      expect(screen.queryByText(/not a supported workflow import file/i)).not.toBeInTheDocument();
      expect(screen.queryByText("Drop workflow package to import")).not.toBeInTheDocument();
      expect(importPreviewWasRequested()).toBe(false);
    } finally {
      uploadTarget.remove();
    }
  });

  it("ignores unsupported files when a local upload drop handler already handled the event", async () => {
    render(<App />);

    await screen.findByText("Built-in Workflows");
    const localDropTarget = document.createElement("div");
    const preventDefault = (event: DragEvent) => event.preventDefault();
    localDropTarget.addEventListener("dragenter", preventDefault);
    localDropTarget.addEventListener("dragover", preventDefault);
    localDropTarget.addEventListener("drop", preventDefault);
    document.body.appendChild(localDropTarget);
    try {
      const dataTransfer = fileDataTransfer([new File(["audio"], "local-audio.wav", { type: "audio/wav" })]);
      fireEvent.dragEnter(localDropTarget, { dataTransfer });
      fireEvent.dragOver(localDropTarget, { dataTransfer });
      fireEvent.drop(localDropTarget, { dataTransfer });

      expect(screen.queryByText(/not a supported workflow import file/i)).not.toBeInTheDocument();
      expect(screen.queryByText("Drop workflow package to import")).not.toBeInTheDocument();
      expect(importPreviewWasRequested()).toBe(false);
    } finally {
      localDropTarget.removeEventListener("dragenter", preventDefault);
      localDropTarget.removeEventListener("dragover", preventDefault);
      localDropTarget.removeEventListener("drop", preventDefault);
      localDropTarget.remove();
    }
  });

  it("sends raw ComfyUI .json drops through workflow import preview", async () => {
    importPreviewResponses.set("raw-workflow.json", {
      import_session_id: null,
      workflow_id: "raw_workflow",
      status: "needs_input_setup",
      user_facing_message: "Needs input setup",
      workflow: {
        id: "raw_workflow",
        name: "Raw Workflow",
        version: "0.1.0",
        description: "",
      },
      required_model_count: 0,
      custom_node_count: 0,
      unresolved_input_count: 1,
      model_summary: null,
    });
    render(<App />);

    await screen.findByText("Built-in Workflows");
    dropFiles([new File(["{}"], "raw-workflow.json", { type: "application/json" })]);

    await waitFor(() => expect(importPreviewWasRequested("raw-workflow.json")).toBe(true));
    expect(screen.queryByText(/not a supported workflow import file/i)).not.toBeInTheDocument();
  });

  it("ignores dashboard builder widget drags so global import does not intercept them", async () => {
    workflowListSummary = {
      ...workflowSummary,
      status: "needs_input_setup",
      status_label: "Needs input setup",
      needs_setup: true,
      dashboard_status: "not_configured",
      dashboard_ready: false,
    };
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    expect(await screen.findByRole("heading", { name: /Dashboard Builder/i })).toBeInTheDocument();
    const widget = await screen.findByTestId("created-widget-ctrl-node-6-text");
    const dataTransfer = internalWidgetDragDataTransfer();

    fireEvent.dragStart(widget, { dataTransfer });
    fireEvent.dragEnter(window, { dataTransfer });
    fireEvent.dragOver(window, { dataTransfer });
    fireEvent.drop(window, { dataTransfer });

    expect(dataTransfer.setData).toHaveBeenCalledWith("text/plain", expect.any(String));
    expect(screen.queryByText("Drop workflow package to import")).not.toBeInTheDocument();
    expect(importPreviewWasRequested()).toBe(false);
  });

  it("restores a workflow route only when its persisted workflow tab still exists", async () => {
    window.localStorage.setItem(
      "noofy.workflowTabs.v1",
      JSON.stringify([{ workflowId: "text_to_image_v0", workflowName: "Text to Image", lastActivatedAt: 1 }]),
    );
    window.localStorage.setItem(
      "noofy.appRoute.v1",
      JSON.stringify({ name: "workflow", workflowId: "text_to_image_v0" }),
    );

    render(<App />);

    expect(await screen.findByRole("button", { name: "Run workflow" })).toBeInTheDocument();

    cleanup();
    window.localStorage.removeItem("noofy.workflowTabs.v1");
    render(<App />);

    expect(await screen.findByText("Built-in Workflows")).toBeInTheDocument();
  });

  it("keeps workflow progress visible across pages and avoids duplicate bars", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));

    await waitFor(() => {
      expect(screen.getByRole("progressbar", { name: "Workflow progress" })).toHaveAttribute("aria-valuenow", "20");
    });

    fireEvent.click(screen.getByRole("button", { name: "Go to home" }));

    await waitFor(() => {
      expect(screen.getByRole("progressbar", { name: "Workflow progress" })).toHaveAttribute("aria-valuenow", "20");
    });
    expect(screen.getAllByRole("progressbar", { name: "Workflow progress" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Text to Image" }));
    // Run stays enabled during the active run; pressing it queues another run.
    expect(await screen.findByRole("button", { name: "Run workflow" })).toBeEnabled();
    expect(screen.getAllByRole("progressbar", { name: "Workflow progress" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Go to home" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop and close" }));

    await waitFor(() => {
      expect(screen.queryByRole("progressbar", { name: "Workflow progress" })).not.toBeInTheDocument();
    });
  });

  it("clears an unknown run handle with a calm inline recovery notice", async () => {
    jobProgressStatus = "unknown";
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/jobs/job-1/progress"))).toBe(true);
    });
    expect(await screen.findByText("This run is no longer active. Run this workflow again when ready.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /workflow failed/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run workflow" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("This run is no longer active. Run this workflow again when ready.")).not.toBeInTheDocument();
  });

  it("closing an inactive tab leaves the current workflow route alone", async () => {
    window.localStorage.setItem(
      "noofy.workflowTabs.v1",
      JSON.stringify([
        { workflowId: "text_to_image_v0", workflowName: "Text to Image", lastActivatedAt: 1 },
        { workflowId: "other_workflow", workflowName: "Other Workflow", lastActivatedAt: 2 },
      ]),
    );

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Text to Image" }));
    expect(await screen.findByRole("button", { name: "Run workflow" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close Other Workflow workspace tab" }));

    expect(screen.getByRole("button", { name: "Run workflow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Text to Image" })).toHaveAttribute("aria-current", "page");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Other Workflow" })).not.toBeInTheDocument();
    });
  });

  it("confirms before closing a tab with active workflow work and cancels only after confirmation", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/jobs/job-1/progress"))).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    expect(await screen.findByRole("dialog", { name: "Stop this workflow?" })).toBeInTheDocument();
    const cancelAllUrl = "/api/workflows/text_to_image_v0/runs/cancel-active-and-queued";
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith(cancelAllUrl))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Stop this workflow?" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close Text to Image workspace tab" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith(cancelAllUrl))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop and close" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith(cancelAllUrl))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Close Text to Image workspace tab" })).not.toBeInTheDocument();
    });
  });

  it("closes an idle tab without confirmation, closes its lease, and never stops the runner", async () => {
    leaseAvailable = true;
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    await screen.findByRole("button", { name: "Run workflow" });
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) =>
        String(url).endsWith("/api/workflows/text_to_image_v0/runner/leases")
        && (init as RequestInit | undefined)?.method === "POST",
      )).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Close Text to Image workspace tab" })).not.toBeInTheDocument();
    });

    // The backend is told the view closed...
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, init]) =>
        String(url).endsWith("/api/workflows/text_to_image_v0/runner/leases/lease-1")
        && (init as RequestInit | undefined)?.method === "DELETE",
      )).toBe(true);
    });
    // ...but nothing is canceled or force-stopped from the frontend: release
    // is the backend's deferred, cooldown-gated decision.
    expect(screen.queryByRole("dialog", { name: "Stop this workflow?" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("cancel"))).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/runner/stop"))).toBe(false);
  });

  it("keeps the tab and lease open when the backend cannot cancel every workflow run", async () => {
    leaseAvailable = true;
    failedCancelCount = 1;
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));
    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));

    const dialog = await screen.findByRole("dialog", { name: "Stop this workflow?" });
    expect(dialog).toHaveTextContent("stop its active generations");
    fireEvent.click(screen.getByRole("button", { name: "Stop and close" }));

    expect(await screen.findByText(/could not stop every active or queued generation/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close Text to Image workspace tab" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).includes("/runner/leases/lease-1")
      && (init as RequestInit | undefined)?.method === "DELETE",
    )).toBe(false);
  });
});
