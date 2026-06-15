import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "../app/AppLayout";
import { RuntimeStatusProvider } from "../app/RuntimeStatusProvider";
import { PENDING_IMPORTED_SETUP_STORAGE_KEY } from "../home/pendingSetupBanners";
import { dashboardDraftKey } from "../dashboard-builder/dashboardBuilderContent";
import { WorkflowLibraryProvider } from "../home/WorkflowLibraryProvider";
import { WorkflowsPage } from "./WorkflowsPage";
import { WORKFLOW_CATEGORY_OPTIONS } from "./workflowMetadataOptions";

const globalCss = readFileSync(resolve(process.cwd(), "src/styles/workflows.css"), "utf8");

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const highHardwareWarning = {
  severity: "high",
  confidence: "medium",
  exceeds_machine_capacity: true,
  reason_codes: ["model_size_heuristic", "estimated_vram_capacity_risk"],
  estimate: {
    estimated_peak_vram_mb: 12_000,
    estimated_peak_ram_mb: null,
    source: "heuristic",
    confidence: "low",
  },
  machine_signal: {
    backend: "cuda",
    memory_pressure: "low",
    total_vram_mb: 8_000,
    free_vram_mb: 7_000,
    total_ram_mb: 32_000,
    free_ram_mb: 22_000,
    signal_quality: "backend_api",
  },
  evidence: {
    local_successful_runs: 0,
    local_memory_error_runs: 0,
    local_input_profile_match: "none",
    creator_observation_available: false,
    model_size_heuristic_available: true,
    required_model_size_mb: 12_000,
  },
  developer_details: {
    reason_codes: ["model_size_heuristic", "estimated_vram_capacity_risk"],
    estimate_source: "heuristic",
  },
};

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
    can_export_noofy: true,
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
    hardware_warning: highHardwareWarning,
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

const longWorkflow = {
  id: "long_workflow",
  name: "Extremely Long Cinematic Portrait Restoration Workflow With Multi Stage Detail Recovery And Edge Case Friendly Naming",
  version: "1.0.0",
  description:
    "A deliberately long workflow description that should remain readable for a few lines without forcing the workflow list wider than the available page area when the details panel is open.",
  icon: "sparkles",
  source_label: "Imported",
  main_model: {
    name: "very-long-main-checkpoint-name-with-many-descriptive-segments-and-version-identifiers-final-production.safetensors",
    type: "checkpoint",
    size_bytes: 2,
  },
  category: "Character Consistency With A Long Category Label",
  last_opened: "2026-05-10T12:00:00Z",
  tags: ["portrait-restoration-with-long-tag", "multi-stage-detail-recovery"],
  missing_model_count: 0,
  needs_setup: false,
  can_remove: true,
  can_export_noofy: true,
  can_export_comfyui_json: true,
  status: "imported",
  status_label: "Imported",
};

const longDetails = {
  ...longWorkflow,
  overview: {
    description: longWorkflow.description,
    author: "Artist",
    website: "https://example.test",
    source: "Imported",
    version: "1.0.0",
  },
  models_used: [
    {
      name: longWorkflow.main_model.name,
      type: "checkpoint",
      size_bytes: 2,
      status: "installed",
      status_label: "Ready",
      folder: "checkpoints",
      source_path: null,
    },
  ],
  run_history: details.run_history,
  organization: {
    category: longWorkflow.category,
    tags: longWorkflow.tags,
    icon: "sparkles",
  },
  advanced: details.advanced,
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

const missingModelImportPreview = {
  import_session_id: "import-session-1",
  workflow_id: "missing_model_flow",
  status: "imported",
  user_facing_message: "Workflow import is ready.",
  workflow: {
    id: "missing_model_flow",
    name: "Missing Model Flow",
    version: "1.0.0",
    description: "Needs a model.",
    icon: "sparkles",
    source_label: "Imported",
    main_model: { name: "missing.safetensors", type: "checkpoint", size_bytes: 1024 },
    category: "Txt2img",
    last_opened: null,
    tags: [],
    missing_model_count: 1,
    needs_setup: false,
    can_remove: true,
    can_export_noofy: true,
    can_export_comfyui_json: true,
    status: "imported",
    status_label: "Imported",
  },
  required_model_count: 1,
  custom_node_count: 0,
  unresolved_input_count: 0,
  duplicate_identity: null,
  model_summary: {
    workflow_id: "missing_model_flow",
    total_count: 1,
    available_count: 0,
    possible_match_count: 0,
    missing_count: 1,
    needs_manual_download_count: 0,
    ready_to_run: false,
    models: [
      {
        requirement_id: "model-1",
        node_id: "4",
        node_type: "CheckpointLoaderSimple",
        input_name: "ckpt_name",
        filename: "missing.safetensors",
        model_type: "checkpoint",
        folder: "checkpoints",
        verification_level: "filename_only",
        size_bytes: 1024,
        source_urls: ["https://example.test/missing.safetensors"],
        source_availability: "known",
        status: "missing",
        status_label: "Missing",
        asset_ownership: "noofy",
        source_path: null,
        matched_root: null,
        matched_sha256: null,
        matched_size_bytes: null,
        message: null,
      },
    ],
  },
};

describe("WorkflowsPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();
  const onOpenWorkflow = vi.fn();
  const onConfigureDashboard = vi.fn();
  const onEditWidgets = vi.fn();
  const onEditDashboard = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
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
      if (url.endsWith("/api/workflow-icons")) {
        return Promise.resolve(jsonResponse({
          icons: [{
            id: "asset:custom-icon.png",
            asset_id: "custom-icon.png",
            label: "Custom icon",
            kind: "custom",
            url: "/api/assets/custom-icon.png",
          }],
        }));
      }
      if (url.endsWith("/api/workflows/imported_cleanup/details")) {
        return Promise.resolve(jsonResponse(details));
      }
      if (url.endsWith("/api/workflows/long_workflow/details")) {
        return Promise.resolve(jsonResponse(longDetails));
      }
      if (url.endsWith("/api/workflows/imported_cleanup/metadata")) {
        return Promise.resolve(jsonResponse({ workflow_id: "imported_cleanup", metadata: {} }));
      }
      if (url.endsWith("/api/workflows/imported_cleanup/package")) {
        return Promise.resolve(jsonResponse(workflowPackage));
      }
      if (url.endsWith("/api/workflows/imported_cleanup") && method === "DELETE") {
        return Promise.resolve(jsonResponse({ workflow_id: "imported_cleanup", removed: true }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function renderPage(options: { initialSearchQuery?: string } = {}) {
    return render(
      <RuntimeStatusProvider>
        <WorkflowLibraryProvider>
          <SidebarProvider>
            <WorkflowsPage
              onNavigate={onNavigate}
              onOpenWorkflow={onOpenWorkflow}
              onConfigureDashboard={onConfigureDashboard}
              onEditWidgets={onEditWidgets}
              onEditDashboard={onEditDashboard}
              initialSearchQuery={options.initialSearchQuery}
            />
          </SidebarProvider>
        </WorkflowLibraryProvider>
      </RuntimeStatusProvider>,
    );
  }

  function workflowRowNames() {
    return Array.from(document.querySelectorAll(".workflow-row .model-name-text")).map((element) => element.textContent);
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

  it("shows quick category tabs only for categories in the current library", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();

    const categoryFilter = screen.getByRole("combobox", { name: "Filter by category" }) as HTMLSelectElement;
    expect(Array.from(categoryFilter.options).map((option) => option.value)).toEqual([
      "all",
      ...WORKFLOW_CATEGORY_OPTIONS,
    ]);

    const categoryTabs = within(screen.getByRole("tablist", { name: "Filter by workflow category" })).getAllByRole("tab");
    expect(categoryTabs.map((tab) => tab.textContent)).toEqual(["All", "Inpainting", "Txt2img"]);
    expect(screen.queryByRole("tab", { name: "txt2audio" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Inpainting" }));

    expect(screen.queryByText("Native Text")).not.toBeInTheDocument();
    expect(screen.getByText("Cleanup Flow")).toBeInTheDocument();
  });

  it("shows definite over-capacity warning pills without disabling Open", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    const row = rowTitle.closest("article")!;
    const pill = within(row).getByText("Not enough memory");
    const openButton = within(row).getByRole("button", { name: "Open" });

    expect(pill).toHaveAttribute(
      "title",
      "This workflow needs about 11.7 GB VRAM, but this machine has 7.8 GB. Lower-memory settings or a lighter workflow may be required.",
    );
    expect(openButton).not.toBeDisabled();

    fireEvent.click(openButton);

    expect(onOpenWorkflow).toHaveBeenCalledWith("imported_cleanup");
  });

  it("sorts workflows by sortable headers without clearing the current search", async () => {
    renderPage();

    expect(await screen.findByText("Native Text")).toBeInTheDocument();
    expect(workflowRowNames()).toEqual(["Cleanup Flow", "Native Text"]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Name descending" }));
    expect(workflowRowNames()).toEqual(["Native Text", "Cleanup Flow"]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Name ascending" }));
    expect(workflowRowNames()).toEqual(["Cleanup Flow", "Native Text"]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Category ascending" }));
    expect(workflowRowNames()).toEqual(["Cleanup Flow", "Native Text"]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Main model ascending" }));
    expect(workflowRowNames()).toEqual(["Cleanup Flow", "Native Text"]);

    fireEvent.click(screen.getByRole("button", { name: "Sort by Main model descending" }));
    expect(workflowRowNames()).toEqual(["Native Text", "Cleanup Flow"]);

    fireEvent.change(screen.getByPlaceholderText("Search workflows..."), { target: { value: "flow" } });
    fireEvent.click(screen.getByRole("button", { name: "Sort by Status ascending" }));

    expect(workflowRowNames()).toEqual(["Cleanup Flow"]);
    expect(screen.getByPlaceholderText("Search workflows...")).toHaveValue("flow");
  });

  it("renders custom imported workflow icons in the row", async () => {
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
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse({ cpu: null, memory: null, vram: null }));
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(jsonResponse([{ ...workflows[1], icon: "asset:custom-icon.png", trust_level: "quarantined_community" }]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const view = renderPage();

    expect(await screen.findByText("Cleanup Flow")).toBeInTheDocument();
    const icon = view.container.querySelector(".workflow-row .model-type-icon img") as HTMLImageElement | null;
    expect(icon?.src).toContain("/api/assets/custom-icon.png");
  });

  it("applies an incoming search query immediately", async () => {
    renderPage({ initialSearchQuery: "cleanup" });

    expect(await screen.findByRole("heading", { name: "Workflows" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search workflows...")).toHaveValue("cleanup");
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

  it("selects workflows without opening their details", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Cleanup Flow" }));

    expect(screen.getByText("1 selected")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/workflows/imported_cleanup/details", expect.anything());
  });

  it("selects all filtered workflows and clears the selection", async () => {
    renderPage();

    await screen.findByText("Cleanup Flow");
    fireEvent.change(screen.getByPlaceholderText("Search workflows..."), { target: { value: "cleanup" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all workflows" }));

    expect(screen.getByRole("checkbox", { name: "Select Cleanup Flow" })).toBeChecked();
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("1 selected")).not.toBeInTheDocument();
  });

  it("shows workflow details in a closeable side panel", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);

    const panel = await screen.findByRole("complementary", { name: "Details for Cleanup Flow" });
    expect(panel).toHaveClass("workflow-detail-drawer");
    expect(screen.queryByRole("button", { name: "Save details" })).not.toBeInTheDocument();
    const noofyExport = screen.getByRole("button", { name: "Export the Noofy workflow" });
    const comfyExport = screen.getByRole("button", { name: "Export ComfyUI JSON" });
    const exportActions = noofyExport.closest(".workflow-detail-export-actions");
    expect(exportActions).not.toBeNull();
    expect(comfyExport.closest(".workflow-detail-export-actions")).toBe(exportActions);
    expect(Array.from(exportActions!.querySelectorAll("button")).map((action) => action.textContent?.trim())).toEqual([
      "Export the Noofy workflow",
      "Export ComfyUI JSON",
    ]);
    expect(screen.getByRole("button", { name: "Close workflow details" }).closest(".workflow-detail-sticky-top")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Open Workflow" }).closest(".workflow-detail-sticky-top")).not.toBeNull();
    await waitFor(() => {
      expect(panel).toHaveClass("workflow-detail-drawer--open");
    });

    fireEvent.click(screen.getByRole("button", { name: "Close workflow details" }));

    await waitFor(() => {
      expect(screen.queryByRole("complementary", { name: "Details for Cleanup Flow" })).not.toBeInTheDocument();
    });
  });

  it("shows beginner hardware compatibility details in the drawer", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);

    expect(await screen.findByText("Hardware compatibility")).toBeInTheDocument();
    expect(screen.getByText("This workflow needs about 11.7 GB VRAM, but this machine has 7.8 GB. Lower-memory settings or a lighter workflow may be required.")).toBeInTheDocument();
    expect(screen.getByText("11.7 GB VRAM")).toBeInTheDocument();
    expect(screen.getByText("6.8 GB VRAM free of 7.8 GB")).toBeInTheDocument();
    expect(screen.getByText("Model size estimate and current machine")).toBeInTheDocument();
    expect(screen.getByText("Developer details")).toBeInTheDocument();
  });

  it("keeps the drawer-open workflow list responsive with long row content", async () => {
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
        return Promise.resolve(jsonResponse([longWorkflow]));
      }
      if (url.endsWith("/api/workflows/long_workflow/details")) {
        return Promise.resolve(jsonResponse(longDetails));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderPage();

    const rowTitle = await screen.findByText(longWorkflow.name);
    fireEvent.click(rowTitle.closest("article")!);

    const panel = await screen.findByRole("complementary", { name: `Details for ${longWorkflow.name}` });
    await waitFor(() => {
      expect(panel).toHaveClass("workflow-detail-drawer--open");
    });

    const layout = document.querySelector(".workflows-layout");
    const listArea = document.querySelector(".workflows-list-area") as HTMLElement;
    const row = rowTitle.closest(".workflow-row") as HTMLElement;
    const modelCell = screen.getByTitle(longWorkflow.main_model.name);
    const descriptionCell = screen.getByTitle(longWorkflow.description);
    const categoryBadge = screen.getByTitle(longWorkflow.category);
    const categorySelect = screen.getByRole("combobox", { name: "Category" }) as HTMLSelectElement;

    expect(layout).toHaveClass("workflows-layout--drawer-open");
    expect(modelCell).toHaveClass("workflow-col-model");
    expect(descriptionCell).toHaveClass("workflow-col-description");
    expect(categoryBadge).toHaveClass("workflow-category-badge");
    expect(Array.from(categorySelect.options).map((option) => option.value)).toEqual([...WORKFLOW_CATEGORY_OPTIONS]);
    expect(categorySelect).toHaveValue(WORKFLOW_CATEGORY_OPTIONS[0]);
    expect(listArea).toHaveClass("workflows-list-area");
    expect(row).toHaveClass("workflow-row");
    expect(globalCss).toContain("container: workflows-list / inline-size;");
    expect(globalCss).toContain("@container workflows-list (max-width: 620px)");
    expect(globalCss).toContain("overflow-x: clip;");
    expect(globalCss).toContain("minmax(0, 1.15fr)");
    expect(globalCss).toContain("grid-template-areas:");
    expect(globalCss).toContain(".workflows-layout--drawer-open .workflow-page-actions");
    expect(globalCss).toContain("width: fit-content;");
  });

  it("persists edited metadata when a details field loses focus", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);

    const workflowName = await screen.findByLabelText("Workflow name");
    fireEvent.change(workflowName, { target: { value: "Edited Cleanup Flow" } });
    fireEvent.blur(workflowName);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"display_name":"Edited Cleanup Flow"'),
        }),
      );
    });

    const description = await screen.findByLabelText("Description");
    fireEvent.change(description, { target: { value: "Updated cleanup description." } });
    fireEvent.blur(description);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining("Updated cleanup description."),
        }),
      );
    });

    const category = await screen.findByRole("combobox", { name: "Category" }) as HTMLSelectElement;
    expect(Array.from(category.options).map((option) => option.value)).toEqual([...WORKFLOW_CATEGORY_OPTIONS]);
    expect(category).toHaveValue("Inpainting");

    fireEvent.change(category, { target: { value: "Restoration" } });
    fireEvent.blur(category);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"category":"Restoration"'),
        }),
      );
    });
  });

  it("uses a visual icon picker in workflow details and persists icon choices", async () => {
    renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);

    expect(await screen.findByRole("radiogroup", { name: "Workflow icon" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import icon" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "Controls" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"icon":"sliders"'),
        }),
      );
    });

    fireEvent.click(screen.getByRole("radio", { name: "Custom icon" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"icon":"asset:custom-icon.png"'),
        }),
      );
    });
  });

  it("imports a custom icon from workflow details and selects it", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse({ cpu: null, memory: null, vram: null }));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse(workflows));
      if (url.endsWith("/api/workflow-icons") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({
          id: "asset:imported-icon.png",
          asset_id: "imported-icon.png",
          label: "imported-icon.png",
          kind: "custom",
          url: "/api/assets/imported-icon.png",
        }));
      }
      if (url.endsWith("/api/workflow-icons")) return Promise.resolve(jsonResponse({ icons: [] }));
      if (url.endsWith("/api/workflows/imported_cleanup/details")) return Promise.resolve(jsonResponse(details));
      if (url.endsWith("/api/workflows/imported_cleanup/metadata")) return Promise.resolve(jsonResponse({ workflow_id: "imported_cleanup", metadata: {} }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const view = renderPage();

    const rowTitle = await screen.findByText("Cleanup Flow");
    fireEvent.click(rowTitle.closest("article")!);
    await screen.findByRole("button", { name: "Import icon" });

    const fileInput = view.container.querySelector('input[accept="image/png,image/jpeg,image/webp,image/gif"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(["png"], "imported-icon.png", { type: "image/png" })] } });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup/metadata",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"icon":"asset:imported-icon.png"'),
        }),
      );
    });
  });

  it("omits remove workflow for native workflows and routes row actions", async () => {
    renderPage();

    await screen.findByText("Native Text");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));

    expect(screen.queryByRole("menuitem", { name: /remove workflow/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Export the Noofy workflow" }));
    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filename")).toHaveValue("Native Text.noofy");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));
    fireEvent.click(screen.getAllByRole("menuitem", { name: "Open" })[0]);
    expect(onOpenWorkflow).toHaveBeenCalledWith("native_text");
  });

  it("clears the imported setup reminder when an imported workflow is removed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.localStorage.setItem(dashboardDraftKey("imported_cleanup"), "stale draft");
    window.localStorage.setItem(
      PENDING_IMPORTED_SETUP_STORAGE_KEY,
      JSON.stringify([{ workflowId: "imported_cleanup", workflowName: "Cleanup Flow", dismissed: false }]),
    );
    renderPage();

    await screen.findByText("Cleanup Flow");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Cleanup Flow" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove workflow" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(window.localStorage.getItem(dashboardDraftKey("imported_cleanup"))).toBeNull();
    expect(window.localStorage.getItem(PENDING_IMPORTED_SETUP_STORAGE_KEY)).toBeNull();
  });

  it("shows a warning and keeps the workflow visible when removal fails", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await screen.findByText("Cleanup Flow");
    fetchMock.mockRejectedValueOnce(new Error("Workflow removal failed."));
    fireEvent.click(screen.getByRole("button", { name: "Actions for Cleanup Flow" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove workflow" }));

    expect(await screen.findByText("Workflow removal failed.")).toBeInTheDocument();
    expect(screen.getByText("Cleanup Flow")).toBeInTheDocument();
  });

  it("removes selected imported workflows and skips native workflows", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.localStorage.setItem(
      PENDING_IMPORTED_SETUP_STORAGE_KEY,
      JSON.stringify([{ workflowId: "imported_cleanup", workflowName: "Cleanup Flow", dismissed: false }]),
    );
    renderPage();

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select all workflows" }));
    expect(screen.getByText("2 selected, 1 can be removed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove selected" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/imported_cleanup",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/workflows/native_text",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(screen.getByText("Removed 1 workflow from Noofy.")).toBeInTheDocument();
    expect(window.localStorage.getItem(PENDING_IMPORTED_SETUP_STORAGE_KEY)).toBeNull();
  });

  it("renders the workflow action menu outside row layout flow", async () => {
    renderPage();

    const workflowName = await screen.findByText("Native Text");
    const row = workflowName.closest(".workflow-row");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));

    const menu = screen.getByRole("menu");
    expect(menu.closest(".workflow-row")).toBeNull();
    expect(row).not.toContainElement(menu);
    expect(menu).toHaveStyle({ position: "fixed", visibility: "visible" });
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

  it("opens the missing models import flow instead of blocking Workflow page imports", async () => {
    let committed = false;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse({ cpu: null, memory: null, vram: null }));
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(jsonResponse(committed ? [...workflows, missingModelImportPreview.workflow] : workflows));
      }
      if (url.includes("/api/workflows/import/preview") && init?.method === "POST") {
        return Promise.resolve(jsonResponse(missingModelImportPreview));
      }
      if (url.endsWith("/api/workflows/import/import-session-1/commit") && init?.method === "POST") {
        committed = true;
        return Promise.resolve(jsonResponse({ ...missingModelImportPreview, import_session_id: null, model_summary: null }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    const view = renderPage();

    await screen.findByRole("heading", { name: "Workflows" });
    const input = view.container.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["noofy"], "missing-model.noofy", { type: "application/octet-stream" })] } });

    expect(await screen.findByRole("dialog", { name: "Missing Model Flow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Missing Models" })).toBeInTheDocument();
    expect(screen.queryByText(/This workflow needs models before it can be imported/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Continue Without Downloading" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/import/import-session-1/commit",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText("Missing Model Flow was added to your local workflows.")).toBeInTheDocument();
    expect(screen.getByText("Missing Model Flow")).toBeInTheDocument();
  });

  it("persists setup imports and exposes configure and dismiss actions", async () => {
    const setupWorkflow = {
      ...missingModelImportPreview.workflow,
      id: "remove_background",
      name: "Remove_background",
      missing_model_count: 0,
      needs_setup: true,
      status_label: "Needs input setup",
    };
    const setupImport = {
      ...missingModelImportPreview,
      import_session_id: null,
      workflow_id: setupWorkflow.id,
      status: "needs_input_setup",
      user_facing_message: "Needs input setup",
      workflow: setupWorkflow,
      required_model_count: 0,
      unresolved_input_count: 1,
      model_summary: null,
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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
        return Promise.resolve(jsonResponse([...workflows, setupWorkflow]));
      }
      if (url.includes("/api/workflows/import/preview") && init?.method === "POST") {
        return Promise.resolve(jsonResponse(setupImport));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const view = renderPage();
    await screen.findByRole("heading", { name: "Workflows" });
    const input = view.container.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["noofy"], "remove-background.noofy", { type: "application/octet-stream" })] },
    });

    expect(await screen.findByText("Remove_background was added to your local workflows.")).toBeInTheDocument();
    await waitFor(() => {
      expect(JSON.parse(window.localStorage.getItem(PENDING_IMPORTED_SETUP_STORAGE_KEY) ?? "[]")).toEqual([
        { workflowId: "remove_background", workflowName: "Remove_background", dismissed: false },
      ]);
    });

    fireEvent.click(screen.getByRole("button", { name: "Configure dashboard" }));
    expect(onConfigureDashboard).toHaveBeenCalledWith("remove_background", "Remove_background");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss setup for Remove_background" }));
    expect(screen.queryByText("Remove_background was added to your local workflows.")).not.toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(PENDING_IMPORTED_SETUP_STORAGE_KEY) ?? "[]")).toEqual([
      { workflowId: "remove_background", workflowName: "Remove_background", dismissed: true },
    ]);
  });
});
