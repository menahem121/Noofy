import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimeStatusProvider, type RuntimeHealthState } from "../app/RuntimeStatusProvider";
import { HomePage } from "./HomePage";
import { WorkflowLibraryProvider } from "./WorkflowLibraryProvider";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

const readyRuntime = {
  mode: "managed",
  reachable: true,
  base_url: "http://127.0.0.1:8188",
  repo_dir: "/tmp/ComfyUI",
  managed_process_running: true,
  pid: 123,
  error: null,
  environment: null,
};

const resourceSnapshot = {
  observed_at: "2026-05-08T10:00:00+00:00",
  cpu: { available: true, percent: 23, used_mb: null, total_mb: null, free_mb: null, source: "test", error: null },
  ram: { available: true, percent: 35, used_mb: 11_264, total_mb: 32_768, free_mb: 21_504, source: "test", error: null },
  vram: { available: false, percent: null, used_mb: null, total_mb: null, free_mb: null, source: null, error: "vram_unavailable" },
  backend: "cpu",
  device_name: null,
  memory_pressure: "low",
};

const readyRuntimeState: Partial<RuntimeHealthState> = {
  backendStatus: "reachable",
  engineStatus: "ready",
  runtime: readyRuntime as RuntimeHealthState["runtime"],
  hasKnownState: true,
  lastCheckedAt: Date.now(),
};

const cachedImportedWorkflow = {
  id: "cached_workflow",
  name: "Cached Workflow",
  version: "0.1.0",
  description: "Previously loaded workflow.",
  trust_level: "noofy_verified",
  status: "imported",
  status_label: "Imported",
};

const mediumHardwareWarning = {
  severity: "medium",
  confidence: "low",
  reason_codes: ["temporary_low_free_memory"],
  estimate: {
    estimated_peak_vram_mb: null,
    estimated_peak_ram_mb: null,
    source: "unknown",
    confidence: null,
  },
  machine_signal: {
    backend: "cuda",
    memory_pressure: "low",
    total_vram_mb: 12_000,
    free_vram_mb: 1_500,
    total_ram_mb: 64_000,
    free_ram_mb: 48_000,
    signal_quality: "backend_api",
  },
  evidence: {
    local_successful_runs: 0,
    local_memory_error_runs: 0,
    local_input_profile_match: "none",
    creator_observation_available: false,
    model_size_heuristic_available: false,
    required_model_size_mb: null,
  },
  developer_details: {},
};

const searchableWorkflows = [
  {
    id: "native_text",
    name: "Native Text",
    version: "1.0.0",
    description: "Generate an image from a prompt.",
    trust_level: "noofy_verified",
    source_label: "Native Noofy",
    category: "Txt2img",
    tags: ["starter"],
    status: "installed",
    status_label: "Installed",
  },
  {
    id: "imported_cleanup",
    name: "Cleanup Flow",
    version: "1.0.0",
    description: "Clean up images.",
    trust_level: "quarantined_community",
    source_label: "Imported",
    category: "Inpainting",
    tags: ["cleanup"],
    status: "imported",
    status_label: "Imported",
  },
  {
    id: "portrait_restore",
    name: "Portrait Restore",
    version: "1.0.0",
    description: "Repair face details.",
    trust_level: "noofy_verified",
    source_label: "Native Noofy",
    category: "Restoration",
    tags: ["portrait"],
    status: "installed",
    status_label: "Installed",
  },
];

const onOpenWorkflow = vi.fn();
const onNavigate = vi.fn();
const onConfigureDashboard = vi.fn();

function renderHomePage(options: {
  runtimeState?: Partial<RuntimeHealthState>;
  skipInitialRefresh?: boolean;
  workflowState?: ComponentProps<typeof WorkflowLibraryProvider>["initialWorkflowState"];
  onConfigureDashboard?: typeof onConfigureDashboard;
  nativeImportRequest?: ComponentProps<typeof HomePage>["nativeImportRequest"];
} = {}) {
  return render(
    <RuntimeStatusProvider
      initialRuntimeState={options.runtimeState}
      skipInitialRefresh={options.skipInitialRefresh}
    >
      <WorkflowLibraryProvider initialWorkflowState={options.workflowState}>
        <HomePage
          onOpenWorkflow={onOpenWorkflow}
          nativeImportRequest={options.nativeImportRequest}
          onConfigureDashboard={options.onConfigureDashboard}
          onNavigate={onNavigate}
        />
      </WorkflowLibraryProvider>
    </RuntimeStatusProvider>,
  );
}

describe("HomePage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onOpenWorkflow.mockReset();
    onNavigate.mockReset();
    onConfigureDashboard.mockReset();
  });

  function mockSearchableHome() {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse(searchableWorkflows));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  }

  it("loads backend runtime and workflow summaries through the Noofy API", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse(resourceSnapshot));
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "text_to_image_v0",
              name: "Text to Image",
              version: "0.1.0",
              description: "Milestone 1 text-to-image workflow package.",
              trust_level: "noofy_verified",
              hardware_warning: mediumHardwareWarning,
              trust: {
                level: "noofy_verified",
                label: "Noofy Verified",
                summary: "Built or reviewed for Noofy's managed runtime.",
                badge_tone: "verified",
                can_prepare_automatically: true,
                requires_explicit_opt_in: false,
                source_policy: "noofy_verified_sources_only",
                signature_status: "bundled_trusted_core",
              },
            },
          ]),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    expect((await screen.findAllByText("Ready")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("1 built-in workflow available.")).toBeInTheDocument();
    expect(screen.getAllByText("Installed").length).toBeGreaterThan(0);
    expect(screen.getByText("Noofy Verified")).toBeInTheDocument();
    expect(screen.getByText("May be heavy")).toHaveAttribute(
      "title",
      "This workflow may run slowly or fail on this machine, depending on settings and available memory. You can still try it.",
    );
  });

  it("shows matching Home search results while typing", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
    });

    const searchInput = await screen.findByPlaceholderText("Search workflows...");
    fireEvent.change(searchInput, { target: { value: "cleanup" } });

    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByRole("option", { name: /Cleanup Flow/i })).toBeInTheDocument();
    expect(within(listbox).queryByRole("option", { name: /Native Text/i })).not.toBeInTheDocument();
    expect(within(listbox).getByText("Clean up images.")).toBeInTheDocument();
  });

  it("opens a workflow when a Home search result is clicked", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
    });

    fireEvent.change(await screen.findByPlaceholderText("Search workflows..."), { target: { value: "portrait" } });
    fireEvent.click(await screen.findByRole("option", { name: /Portrait Restore/i }));

    expect(onOpenWorkflow).toHaveBeenCalledWith("portrait_restore");
  });

  it("routes Home page primary and view-all actions to their real destinations", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
      onConfigureDashboard,
    });

    fireEvent.click(await screen.findByRole("button", { name: "New Workflow" }));
    expect(onConfigureDashboard).toHaveBeenCalledWith();

    fireEvent.click(screen.getByRole("button", { name: "View workflows" }));
    expect(onNavigate).toHaveBeenCalledWith("workflows");

    fireEvent.click(screen.getByRole("button", { name: "View all" }));
    expect(onNavigate).toHaveBeenCalledWith("workflows");
  });

  it("shows the last three opened workflows from backend workflow state", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return new Promise<Response>(() => {});
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
      workflowState: {
        workflows: [
          {
            id: "workflow_oldest",
            name: "Oldest Opened",
            version: "1.0.0",
            description: "Old open.",
            trust_level: "noofy_verified",
            source_label: "Native Noofy",
            category: "Txt2img",
            status: "installed",
            status_label: "Installed",
            last_opened: "2026-05-20T10:00:00+00:00",
          },
          {
            id: "workflow_second",
            name: "Second Opened",
            version: "1.0.0",
            description: "Second open.",
            trust_level: "noofy_verified",
            source_label: "Imported",
            category: "Restoration",
            status: "imported",
            status_label: "Imported",
            last_opened: "2026-05-28T10:00:00+00:00",
          },
          {
            id: "workflow_first",
            name: "First Opened",
            version: "1.0.0",
            description: "First open.",
            trust_level: "noofy_verified",
            source_label: "Native Noofy",
            category: "Txt2img",
            status: "installed",
            status_label: "Installed",
            last_opened: "2026-05-29T10:00:00+00:00",
          },
          {
            id: "workflow_third",
            name: "Third Opened",
            version: "1.0.0",
            description: "Third open.",
            trust_level: "noofy_verified",
            source_label: "Native Noofy",
            category: "Img2img",
            status: "installed",
            status_label: "Installed",
            last_opened: "2026-05-27T10:00:00+00:00",
          },
        ],
        hasLoaded: true,
        lastLoadedAt: Date.now(),
      },
    });

    const recentSection = screen.getByRole("region", { name: "Recently Opened" });
    const rows = Array.from(recentSection.querySelectorAll(".recent-row:not(.recent-row--empty)"));

    expect(rows).toHaveLength(3);
    expect(rows.map((row) => within(row as HTMLElement).getByRole("heading").textContent)).toEqual([
      "First Opened",
      "Second Opened",
      "Third Opened",
    ]);
    expect(within(recentSection).queryByRole("heading", { name: "Oldest Opened" })).not.toBeInTheDocument();

    fireEvent.click(within(rows[1] as HTMLElement).getByRole("button", { name: "Open" }));
    expect(onOpenWorkflow).toHaveBeenCalledWith("workflow_second");

    // Flush the fire-and-forget /api/resources fetch so its trailing setState lands inside act().
    await act(async () => {});
  });

  it("pressing Enter on Home search navigates to Workflows with the query preserved", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
    });

    const searchInput = await screen.findByPlaceholderText("Search workflows...");
    fireEvent.change(searchInput, { target: { value: "cleanup" } });
    await screen.findByRole("option", { name: /Cleanup Flow/i });
    fireEvent.keyDown(searchInput, { key: "Enter" });

    expect(onNavigate).toHaveBeenCalledWith("workflows", { workflowSearch: "cleanup" });
    expect(onOpenWorkflow).not.toHaveBeenCalled();
  });

  it("keeps Home search empty results quiet and offers the Workflows page", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
    });

    fireEvent.change(await screen.findByPlaceholderText("Search workflows..."), { target: { value: "nothing-here" } });

    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).queryByRole("option")).not.toBeInTheDocument();
    fireEvent.click(within(listbox).getByRole("button", { name: "Go to Workflows page" }));

    expect(onNavigate).toHaveBeenCalledWith("workflows", { workflowSearch: "nothing-here" });
  });

  it("supports keyboard navigation in Home search results", async () => {
    mockSearchableHome();
    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
    });

    const searchInput = await screen.findByPlaceholderText("Search workflows...");
    fireEvent.change(searchInput, { target: { value: "flow" } });
    await screen.findByRole("option", { name: /Cleanup Flow/i });

    fireEvent.keyDown(searchInput, { key: "ArrowDown" });
    fireEvent.keyDown(searchInput, { key: "Enter" });

    expect(onOpenWorkflow).toHaveBeenCalledWith("imported_cleanup");
  });

  it("shows workflow row actions on native backend workflow cards only", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "native_text",
              name: "Native Text",
              version: "1.0.0",
              description: "Built in workflow.",
              trust_level: "noofy_verified",
              source_label: "Native Noofy",
              status: "installed",
              status_label: "Installed",
              can_remove: false,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
          ]),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByRole("heading", { name: "Native Text" });
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));

    expect(screen.getByRole("menuitem", { name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "View details" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Edit dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Edit Widgets" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "Export .Noofy" }));
    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filename")).toHaveValue("Native Text.noofy");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Export ComfyUI JSON" }));
    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filename")).toHaveValue("Native Text.json");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Actions for Native Text" }));
    expect(screen.queryByRole("menuitem", { name: "Remove workflow" })).not.toBeInTheDocument();
  });

  it("groups native Text to Image and Image to Image variants behind Home page model selectors", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "native_txt_sdxl",
              name: "Text to Image \u2014 SDXL",
              version: "1.0.0",
              description: "Native SDXL generation.",
              category: "Txt2img",
              trust_level: "noofy_verified",
              source_label: "Native Noofy",
              status: "installed",
              status_label: "Installed",
              can_remove: false,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
            {
              id: "native_txt_flux",
              name: "Text to Image \u2014 Flux",
              version: "1.0.0",
              description: "Native Flux generation.",
              category: "Txt2img",
              trust_level: "noofy_verified",
              source_label: "Native Noofy",
              status: "installed",
              status_label: "Installed",
              can_remove: false,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
            {
              id: "native_img_sdxl",
              name: "Image to Image \u2014 SDXL",
              version: "1.0.0",
              description: "Native SDXL image guidance.",
              category: "Img2img",
              trust_level: "noofy_verified",
              source_label: "Native Noofy",
              status: "installed",
              status_label: "Installed",
              can_remove: false,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
            {
              id: "native_img_flux",
              name: "Image to Image \u2014 Flux",
              version: "1.0.0",
              description: "Native Flux image guidance.",
              category: "Img2img",
              trust_level: "noofy_verified",
              source_label: "Native Noofy",
              status: "installed",
              status_label: "Installed",
              can_remove: false,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
            {
              id: "community_txt",
              name: "Text to Image \u2014 Community",
              version: "1.0.0",
              description: "Imported community generation.",
              category: "Txt2img",
              trust_level: "quarantined_community",
              source_label: "Imported",
              status: "imported",
              status_label: "Imported",
              can_remove: true,
              can_export_noofy: true,
              can_export_comfyui_json: true,
            },
          ]),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    const textSelector = await screen.findByLabelText("Text to Image model workflow");
    const imageSelector = screen.getByLabelText("Image to Image model workflow");

    expect(screen.queryByRole("heading", { name: "Text to Image \u2014 SDXL" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Text to Image \u2014 Flux" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Text to Image \u2014 Community" })).not.toBeInTheDocument();
    expect(textSelector).toHaveValue("native_txt_sdxl");
    expect(within(textSelector).getByRole("option", { name: "Flux" })).toBeInTheDocument();
    expect(imageSelector).toHaveValue("native_img_sdxl");
    expect(within(imageSelector).getByRole("option", { name: "Flux" })).toBeInTheDocument();

    fireEvent.change(textSelector, { target: { value: "native_txt_flux" } });
    const selectedTextSelector = screen.getByLabelText("Text to Image model workflow");
    expect(selectedTextSelector).toHaveValue("native_txt_flux");
    const textCard = selectedTextSelector.closest("article");
    expect(textCard).not.toBeNull();
    fireEvent.click(within(textCard as HTMLElement).getByRole("button", { name: "Open Text to Image" }));

    expect(onOpenWorkflow).toHaveBeenCalledWith("native_txt_flux");
  });

  it("shows starter content and a clear status when the backend is unavailable", async () => {
    fetchMock.mockRejectedValue(new Error("connect failed"));

    renderHomePage();

    expect(await screen.findByText("Workflow library could not refresh")).toBeInTheDocument();
    expect(screen.getAllByText("Service offline")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Reconnect")).toBeInTheDocument();
  });

  it("keeps Ready status visible without putting imported cached workflows in Built-in Workflows", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return new Promise<Response>(() => {});
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
      workflowState: {
        workflows: [cachedImportedWorkflow],
        hasLoaded: true,
        lastLoadedAt: Date.now(),
      },
    });

    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.queryByText("Checking Noofy")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cached Workflow" })).not.toBeInTheDocument();
    expect(screen.getByText("Starter workflows will appear here as packages are added.")).toBeInTheDocument();

    // Flush the fire-and-forget /api/resources fetch so its trailing setState lands inside act().
    await act(async () => {});
  });

  it("keeps imported Noofy re-exports out of Built-in Workflows", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return new Promise<Response>(() => {});
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const view = renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
      workflowState: {
        workflows: [{
          id: "unknown__text2img__0.1.0",
          name: "text2img",
          version: "0.1.0",
          description: "Round-tripped from Noofy.",
          icon: "asset:custom-icon.png",
          trust_level: "quarantined_community",
          trust: {
            level: "quarantined_community",
            label: "Community",
            summary: "Community workflow.",
            badge_tone: "community",
            can_prepare_automatically: true,
            requires_explicit_opt_in: true,
            source_policy: "explicit_opt_in_and_isolated_capsule_required",
            signature_status: "not_required",
          },
          source_label: "Imported",
          status: "imported",
          status_label: "Imported",
        }],
        hasLoaded: true,
        lastLoadedAt: Date.now(),
      },
    });

    expect(screen.queryByRole("heading", { name: "text2img" })).not.toBeInTheDocument();
    expect(screen.queryByText("Community")).not.toBeInTheDocument();
    expect(screen.queryByText("Unsupported")).not.toBeInTheDocument();
    const icon = view.container.querySelector(".workflow-card__icon img") as HTMLImageElement | null;
    expect(icon).toBeNull();

    // Flush the fire-and-forget /api/resources fetch so its trailing setState lands inside act().
    await act(async () => {});
  });

  it("preserves cached workflows and shows a warning when workflow refresh fails", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.reject(new Error("workflow refresh failed"));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage({
      runtimeState: readyRuntimeState,
      skipInitialRefresh: true,
      workflowState: {
        workflows: [cachedImportedWorkflow],
        hasLoaded: true,
        lastLoadedAt: Date.now(),
      },
    });

    expect(await screen.findByText("Workflow library could not refresh")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Cached Workflow" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
  });

  it("previews a .noofy workflow import, commits the staged import, and refreshes workflows", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse(resourceSnapshot));
      }

      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=eraser.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(
          jsonResponse({
            import_session_id: "import-session-1",
            workflow_id: "unknown__eraserv4.5__0.1.0",
            status: "needs_input_setup",
            user_facing_message: "Needs input setup",
            workflow: {
              id: "unknown__eraserv4.5__0.1.0",
              name: "EraserV4.5",
              version: "0.1.0",
              description: "",
              trust_level: "quarantined_community",
              trust: {
                level: "quarantined_community",
                label: "Community",
                summary: "Community workflow prepared only after permission and isolated resolution.",
                badge_tone: "community",
                can_prepare_automatically: true,
                requires_explicit_opt_in: true,
                source_policy: "explicit_opt_in_and_isolated_capsule_required",
                signature_status: "missing",
              },
            },
            required_model_count: 2,
            custom_node_count: 5,
            unresolved_input_count: 1,
            model_summary: {
              workflow_id: "unknown__eraserv4.5__0.1.0",
              total_count: 2,
              available_count: 1,
              possible_match_count: 0,
              missing_count: 1,
              needs_manual_download_count: 0,
              ready_to_run: false,
              models: [
                {
                  requirement_id: "checkpoint",
                  node_id: "1",
                  node_type: "CheckpointLoaderSimple",
                  input_name: "ckpt_name",
                  filename: "eraser-model.safetensors",
                  model_type: "Checkpoint",
                  folder: "checkpoints",
                  verification_level: "filename_only",
                  size_bytes: 2_147_483_648,
                  source_urls: ["https://example.test/eraser-model.safetensors"],
                  source_availability: "known",
                  status: "missing",
                  status_label: "Missing",
                  asset_ownership: "community",
                  source_path: null,
                  matched_root: null,
                  matched_sha256: null,
                  matched_size_bytes: null,
                  message: "Download before running.",
                },
                {
                  requirement_id: "vae",
                  node_id: "2",
                  node_type: "VAELoader",
                  input_name: "vae_name",
                  filename: "vae-ft-mse.safetensors",
                  model_type: "VAE",
                  folder: "vae",
                  verification_level: "filename_only",
                  size_bytes: 334_000_000,
                  source_urls: [],
                  source_availability: "unknown",
                  status: "available",
                  status_label: "Available",
                  asset_ownership: "community",
                  source_path: "/models/vae/vae-ft-mse.safetensors",
                  matched_root: "/models",
                  matched_sha256: null,
                  matched_size_bytes: 334_000_000,
                  message: null,
                },
              ],
            },
          }),
        );
      }

      if (url.endsWith("/api/workflows/import/import-session-1/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            import_session_id: "import-session-1",
            workflow_id: "unknown__eraserv4.5__0.1.0",
            status: "needs_input_setup",
            user_facing_message: "Needs input setup",
            workflow: {
              id: "unknown__eraserv4.5__0.1.0",
              name: "EraserV4.5",
              version: "0.1.0",
              description: "",
              trust_level: "quarantined_community",
              trust: {
                level: "quarantined_community",
                label: "Community",
                summary: "Community workflow prepared only after permission and isolated resolution.",
                badge_tone: "community",
                can_prepare_automatically: true,
                requires_explicit_opt_in: true,
                source_policy: "explicit_opt_in_and_isolated_capsule_required",
                signature_status: "missing",
              },
            },
            required_model_count: 2,
            custom_node_count: 5,
            unresolved_input_count: 1,
            model_summary: null,
          }),
        );
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "unknown__eraserv4.5__0.1.0",
              name: "EraserV4.5",
              version: "0.1.0",
              description: "",
              trust_level: "quarantined_community",
              trust: {
                level: "quarantined_community",
                label: "Community",
                summary: "Community workflow prepared only after permission and isolated resolution.",
                badge_tone: "community",
                can_prepare_automatically: true,
                requires_explicit_opt_in: true,
                source_policy: "explicit_opt_in_and_isolated_capsule_required",
                signature_status: "missing",
              },
              status: "needs_input_setup",
              status_label: "Needs input setup",
              unresolved_input_count: 1,
            },
          ]),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    // Wait for the page to load, then pick a file directly (community preparation is auto-allowed).
    await screen.findByText("Choose File");
    const file = new File(["archive"], "eraser.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/workflows/import/preview?filename=eraser.noofy&allow_unverified_community_preparation=true",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/octet-stream",
          },
          body: expect.any(ArrayBuffer),
        },
      );
    });
    expect(await screen.findByRole("dialog", { name: "EraserV4.5" })).toBeInTheDocument();
    expect(screen.getByText("eraser-model.safetensors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue Without Downloading" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/import-session-1/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: undefined,
      });
    });
    expect((await screen.findAllByText("Needs input setup")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "EraserV4.5" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search workflows..."), { target: { value: "eraser" } });
    expect(await screen.findByRole("option", { name: /EraserV4.5/i })).toBeInTheDocument();
  });

  it("asks before importing a duplicate workflow identity", async () => {
    const duplicateImport = {
      import_session_id: "duplicate-session",
      workflow_id: "unknown__portrait__0.1.0",
      status: "duplicate_identity",
      user_facing_message: "This workflow is already in Noofy. Choose how to import it.",
      workflow: {
        id: "unknown__portrait__0.1.0",
        name: "Portrait Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "quarantined_community",
      },
      required_model_count: 0,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: null,
      duplicate_identity: {
        status: "conflict",
        user_facing_message: "A workflow with this identity already exists in Noofy.",
        existing_workflow: {
          id: "unknown__portrait__0.1.0",
          name: "Portrait Workflow",
          version: "0.1.0",
        },
        incoming_workflow: {
          id: "unknown__portrait__0.1.0",
          name: "Portrait Workflow",
          version: "0.1.0",
        },
        actions: ["replace", "copy", "cancel"],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=portrait.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(duplicateImport));
      }
      if (url.endsWith("/api/workflows/import/duplicate-session/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...duplicateImport,
            import_session_id: null,
            workflow_id: "local__portrait-copy__0.1.0",
            user_facing_message: "Imported",
            workflow: {
              ...duplicateImport.workflow,
              id: "local__portrait-copy__0.1.0",
              name: "Portrait Workflow Copy",
            },
            duplicate_identity: null,
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByText("Choose File");
    const file = new File(["archive"], "portrait.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Portrait Workflow" })).toBeInTheDocument();
    expect(screen.getByText("No silent replacement")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import as Copy" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/duplicate-session/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ duplicate_action: "copy" }),
      });
    });
  });

  it("shows immediate feedback while continuing an import without downloading", async () => {
    const pendingImport = {
      import_session_id: "import-session-slow-commit",
      workflow_id: "slow_model_workflow",
      status: "imported",
      user_facing_message: "Ready to import",
      workflow: {
        id: "slow_model_workflow",
        name: "Slow Model Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "noofy_verified",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: {
        workflow_id: "slow_model_workflow",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 1,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [
          {
            requirement_id: "checkpoint",
            node_id: "1",
            node_type: "CheckpointLoaderSimple",
            input_name: "ckpt_name",
            filename: "slow.safetensors",
            model_type: "Checkpoint",
            folder: "checkpoints",
            verification_level: "sha256_size",
            size_bytes: 1024,
            source_urls: [],
            source_availability: "resolvable",
            status: "missing",
            status_label: "Missing",
            asset_ownership: "external_reference",
            source_path: null,
            matched_root: null,
            matched_sha256: null,
            matched_size_bytes: null,
            message: "Noofy can try to resolve and download this model before the workflow runs.",
          },
        ],
      },
    };
    let resolveCommit!: (response: Response) => void;
    const commitPromise = new Promise<Response>((resolve) => {
      resolveCommit = resolve;
    });

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=slow.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(pendingImport));
      }
      if (url.endsWith("/api/workflows/import/import-session-slow-commit/commit") && init?.method === "POST") {
        return commitPromise;
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByText("Choose File");
    const file = new File(["archive"], "slow.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Slow Model Workflow" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue Without Downloading" }));

    expect(await screen.findByRole("button", { name: "Importing..." })).toBeDisabled();
    expect(screen.getByText("Preparing workflow import...")).toBeInTheDocument();

    resolveCommit(
      jsonResponse({
        ...pendingImport,
        import_session_id: null,
        user_facing_message: "Imported",
        model_summary: null,
      }),
    );
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Slow Model Workflow" })).not.toBeInTheDocument();
    });
  });

  it("moves the missing model download progress bar with the reported percentage", async () => {
    const missingModel = {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "sd15.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: [],
      source_availability: "resolvable",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: "Noofy can try to resolve and download this model before the workflow runs.",
    };
    const pendingImport = {
      import_session_id: "import-session-progress",
      workflow_id: "progress_workflow",
      status: "imported",
      user_facing_message: "Ready to import",
      workflow: {
        id: "progress_workflow",
        name: "Progress Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "noofy_verified",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: {
        workflow_id: "progress_workflow",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 1,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [missingModel],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=progress.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(pendingImport));
      }
      if (url.endsWith("/api/workflows/import/import-session-progress/download-models") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-progress",
            import_session_id: "import-session-progress",
            workflow_id: "progress_workflow",
            status: "queued",
            user_facing_message: "Model download is queued.",
          }),
        );
      }
      if (url.endsWith("/api/workflows/import/import-session-progress/download-models/model-download-progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-progress",
            import_session_id: "import-session-progress",
            workflow_id: "progress_workflow",
            status: "running",
            user_facing_message: "Downloading required models...",
            current_model_filename: "sd15.safetensors",
            current_model_index: 1,
            total_models: 1,
            bytes_downloaded: 384,
            total_bytes: 1024,
            percent: 37.5,
            speed_bytes_per_second: 128,
            models: [
              {
                requirement_id: "checkpoint",
                filename: "sd15.safetensors",
                status: "downloading",
                status_label: "Downloading",
                bytes_downloaded: 384,
                total_bytes: 1024,
                message: null,
              },
            ],
            model_summary: pendingImport.model_summary,
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByText("Choose File");
    const file = new File(["archive"], "progress.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Progress Workflow" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download Missing Models" }));

    expect(await screen.findByText("37.5%")).toBeInTheDocument();
    const progressbar = screen.getByRole("progressbar", { name: "Model download progress" });
    expect(progressbar).toHaveAttribute("aria-valuenow", "37.5");
    expect(progressbar.querySelector(".model-download-progress__bar-fill")).toHaveStyle("width: 37.5%");
  });

  it("opens the model popup while verification runs before allowing a ready import", async () => {
    const checkingModel = {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "ready.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: [],
      source_availability: "known",
      status: "checking",
      status_label: "Checking",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: "Noofy is checking whether this model is already available locally.",
    };
    const availableModel = {
      ...checkingModel,
      status: "available",
      status_label: "Available",
      asset_ownership: "noofy_downloaded",
      source_path: "/models/checkpoints/ready.safetensors",
      matched_root: "/models",
      matched_sha256: "abc",
      matched_size_bytes: 1024,
      message: null,
    };
    const readyImport = {
      import_session_id: "import-session-ready",
      workflow_id: "ready_workflow",
      status: "imported",
      user_facing_message: "Ready to import",
      workflow: {
        id: "ready_workflow",
        name: "Ready Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "noofy_verified",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: {
        workflow_id: "ready_workflow",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 0,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [checkingModel],
      },
    };
    const readySummary = {
      ...readyImport.model_summary,
      available_count: 1,
      ready_to_run: true,
      models: [availableModel],
    };
    let resolveVerification!: (response: Response) => void;
    const verificationPromise = new Promise<Response>((resolve) => {
      resolveVerification = resolve;
    });

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse(resourceSnapshot));
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse([
            {
              id: "ready_workflow",
              name: "Ready Workflow",
              version: "0.1.0",
              description: "",
              trust_level: "noofy_verified",
              status: "imported",
              status_label: "Imported",
            },
          ]),
        );
      }

      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=ready.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(readyImport));
      }

      if (url.endsWith("/api/workflows/import/import-session-ready/model-verification")) {
        return verificationPromise;
      }

      if (url.endsWith("/api/workflows/import/import-session-ready/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...readyImport,
            user_facing_message: "Imported",
            model_summary: null,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByText("Choose File");
    const file = new File(["archive"], "ready.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Ready Workflow" })).toBeInTheDocument();
    expect(await screen.findByRole("progressbar", { name: "Model verification progress" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Missing Models" })).toBeDisabled();

    resolveVerification(
      jsonResponse({
        job_id: "model-verification-ready",
        import_session_id: "import-session-ready",
        workflow_id: "ready_workflow",
        status: "completed",
        user_facing_message: "Model verification finished.",
        current_model_filename: null,
        current_model_index: null,
        total_models: 1,
        verified_models: 1,
        percent: 100,
        models: [availableModel],
        model_summary: readySummary,
      }),
    );
    const readyButton = await screen.findByRole("button", { name: "Open Workflow" });
    expect(screen.queryByRole("progressbar", { name: "Model verification progress" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Missing Models" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue Without Downloading" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel Import" })).not.toBeInTheDocument();
    fireEvent.click(readyButton);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/import-session-ready/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: undefined,
      }));
    expect(screen.queryByRole("dialog", { name: "Ready Workflow" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Ready Workflow" })).not.toBeInTheDocument();
    expect(onOpenWorkflow).toHaveBeenCalledWith("ready_workflow");
  });

  it("starts the staged import flow for a workflow file opened by the operating system", async () => {
    const missingModel = {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "native-open.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: [],
      source_availability: "known",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: "Download before running.",
    };
    const pendingImport = {
      import_session_id: "native-open-session",
      workflow_id: "native_open_workflow",
      status: "imported",
      user_facing_message: "Review models before importing.",
      workflow: {
        id: "native_open_workflow",
        name: "Native Open Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "quarantined_community",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: {
        workflow_id: "native_open_workflow",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 1,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [missingModel],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      if (
        url.endsWith("/api/workflows/import/preview?filename=native-open.noofy&allow_unverified_community_preparation=true") &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(pendingImport));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage({
      nativeImportRequest: {
        id: 1,
        file: new File(["archive"], "native-open.noofy"),
        filename: "native-open.noofy",
      },
    });

    expect(await screen.findByRole("dialog", { name: "Native Open Workflow" })).toBeInTheDocument();
    expect(screen.getByText("native-open.safetensors")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/import/preview?filename=native-open.noofy&allow_unverified_community_preparation=true",
      expect.objectContaining({
        method: "POST",
        body: expect.any(ArrayBuffer),
      }),
    );
  });

  it("opens the workflow after downloaded models make the import ready", async () => {
    const missingModel = {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "sd15.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: [],
      source_availability: "resolvable",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: "Noofy can try to resolve and download this model before the workflow runs.",
    };
    const availableModel = {
      ...missingModel,
      status: "available",
      status_label: "Available",
      source_availability: "known",
      message: null,
    };
    const pendingImport = {
      import_session_id: "import-session-download",
      workflow_id: "core_sd15_txt2img",
      status: "imported",
      user_facing_message: "Ready to import",
      workflow: {
        id: "core_sd15_txt2img",
        name: "Core SD15 Text to Image",
        version: "0.1.0",
        description: "",
        trust_level: "noofy_verified",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 0,
      model_summary: {
        workflow_id: "core_sd15_txt2img",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 1,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [missingModel],
      },
    };

    let statusCalls = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse(resourceSnapshot));
      }

      if (url.endsWith("/api/workflows")) {
        return Promise.resolve(
          jsonResponse(
            init?.method
              ? []
              : [
                  {
                    id: "core_sd15_txt2img",
                    name: "Core SD15 Text to Image",
                    version: "0.1.0",
                    description: "",
                    trust_level: "noofy_verified",
                    status: "imported",
                    status_label: "Imported",
                  },
                ],
          ),
        );
      }

      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=core_sd15_txt2img.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(pendingImport));
      }

      if (
        url.endsWith("/api/workflows/import/import-session-download/download-models") &&
        init?.method === "POST"
      ) {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-1",
            import_session_id: "import-session-download",
            workflow_id: "core_sd15_txt2img",
            status: "queued",
            user_facing_message: "Model download is queued.",
          }),
        );
      }

      if (url.endsWith("/api/workflows/import/import-session-download/download-models/model-download-1")) {
        statusCalls += 1;
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-1",
            import_session_id: "import-session-download",
            workflow_id: "core_sd15_txt2img",
            status: "completed",
            user_facing_message: "Model download check finished.",
            current_model_filename: null,
            current_model_index: null,
            total_models: 1,
            bytes_downloaded: null,
            total_bytes: null,
            percent: null,
            speed_bytes_per_second: null,
            models: [],
            model_summary: {
              workflow_id: "core_sd15_txt2img",
              total_count: 1,
              available_count: 1,
              possible_match_count: 0,
              missing_count: 0,
              needs_manual_download_count: 0,
              ready_to_run: true,
              models: [availableModel],
            },
          }),
        );
      }

      if (url.endsWith("/api/workflows/import/import-session-download/commit") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            ...pendingImport,
            import_session_id: null,
            user_facing_message: "Imported",
            model_summary: null,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage();

    await screen.findByText("Choose File");
    const file = new File(["archive"], "core_sd15_txt2img.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Core SD15 Text to Image" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download Missing Models" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/import-session-download/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: undefined,
      });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Core SD15 Text to Image" })).not.toBeInTheDocument();
    });
    expect(statusCalls).toBe(1);
    expect(onOpenWorkflow).toHaveBeenCalledWith("core_sd15_txt2img");
    expect(screen.queryByRole("heading", { name: "Core SD15 Text to Image" })).not.toBeInTheDocument();
  });

  it("continues to workflow configuration after downloaded models leave input setup remaining", async () => {
    const model = {
      requirement_id: "checkpoint",
      node_id: "1",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "setup.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "sha256_size",
      size_bytes: 1024,
      source_urls: [],
      source_availability: "resolvable",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "external_reference",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: null,
    };
    const pendingImport = {
      import_session_id: "import-session-configure",
      workflow_id: "setup_workflow",
      status: "needs_input_setup",
      user_facing_message: "Needs input setup",
      workflow: {
        id: "setup_workflow",
        name: "Setup Workflow",
        version: "0.1.0",
        description: "",
        trust_level: "noofy_verified",
      },
      required_model_count: 1,
      custom_node_count: 0,
      unresolved_input_count: 1,
      model_summary: {
        workflow_id: "setup_workflow",
        total_count: 1,
        available_count: 0,
        possible_match_count: 0,
        missing_count: 1,
        needs_manual_download_count: 0,
        ready_to_run: false,
        models: [model],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([]));
      if (
        url.endsWith(
          "/api/workflows/import/preview?filename=setup.noofy&allow_unverified_community_preparation=true",
        ) &&
        init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse(pendingImport));
      }
      if (url.endsWith("/api/workflows/import/import-session-configure/download-models") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-configure",
            import_session_id: "import-session-configure",
            workflow_id: "setup_workflow",
            status: "queued",
            user_facing_message: "Model download is queued.",
          }),
        );
      }
      if (url.endsWith("/api/workflows/import/import-session-configure/download-models/model-download-configure")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-configure",
            import_session_id: "import-session-configure",
            workflow_id: "setup_workflow",
            status: "completed",
            user_facing_message: "Model download check finished.",
            current_model_filename: null,
            current_model_index: null,
            total_models: 1,
            bytes_downloaded: null,
            total_bytes: null,
            percent: null,
            speed_bytes_per_second: null,
            models: [],
            model_summary: {
              ...pendingImport.model_summary,
              available_count: 1,
              missing_count: 0,
              ready_to_run: true,
              models: [{ ...model, status: "available", status_label: "Available" }],
            },
          }),
        );
      }
      if (url.endsWith("/api/workflows/import/import-session-configure/commit") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ ...pendingImport, import_session_id: null, model_summary: null }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderHomePage({ onConfigureDashboard });

    await screen.findByText("Choose File");
    const file = new File(["archive"], "setup.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByRole("dialog", { name: "Setup Workflow" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download Missing Models" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/import-session-configure/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: undefined,
      });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Setup Workflow" })).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(onConfigureDashboard).toHaveBeenCalledWith("setup_workflow", "Setup Workflow");
    });
    expect(onOpenWorkflow).not.toHaveBeenCalled();
  });
});
