import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowRunPage } from "./WorkflowRunPage";

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
  environment: { prepared: true },
};

const validWorkflow = {
  workflow_id: "text_to_image_v0",
  valid: true,
  missing_models: [],
  errors: [],
};

const readyModelSummary = {
  workflow_id: "text_to_image_v0",
  total_count: 0,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 0,
  needs_manual_download_count: 0,
  ready_to_run: true,
  models: [],
};

const missingModelSummary = {
  workflow_id: "text_to_image_v0",
  total_count: 1,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 1,
  needs_manual_download_count: 0,
  ready_to_run: false,
  models: [
    {
      requirement_id: "checkpoint",
      node_id: "4",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      filename: "v1-5-pruned-emaonly-fp16.safetensors",
      model_type: "Checkpoint",
      folder: "checkpoints",
      verification_level: "filename_only",
      size_bytes: null,
      source_urls: [],
      source_availability: "unknown",
      status: "missing",
      status_label: "Missing",
      asset_ownership: "community",
      source_path: null,
      matched_root: null,
      matched_sha256: null,
      matched_size_bytes: null,
      message: "Model is missing.",
    },
  ],
};

const workflowStatus = {
  workflow_id: "text_to_image_v0",
  workflow: {
    id: "text_to_image_v0",
    name: "Text to Image",
    version: "0.1.0",
    description: "Generate a new image from a simple text prompt.",
    publisher_id: "noofy",
    package_id: "text_to_image_v0",
    trust_level: "noofy_verified",
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
  install: {},
  required_actions: [],
  compatibility_guidance: [],
  runner: null,
  runner_status: "not_started",
  can_prepare: true,
  can_cancel_preparation: false,
  can_cancel_job: false,
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

const configuredPackageData = {
  metadata: {
    id: "text_to_image_v0",
    name: "Text to Image",
    version: "0.1.0",
    description: "",
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
  outputs: [
    { id: "image_a", label: "Image A", node_id: "9", type: "image" },
    { id: "image_b", label: "Image B", node_id: "10", type: "image" },
  ],
  dashboard: {
    version: "0.1.0",
    status: "configured",
    sections: [
      {
        id: "main",
        title: "Main",
        controls: [
          {
            id: "prompt",
            type: "textarea",
            label: "Prompt",
            input_id: "prompt",
            layout: { x: 0, y: 0, w: 16, h: 6 },
          },
          {
            id: "result-a",
            type: "display_image",
            label: "Result A",
            output_id: "image_a",
            show_download: true,
            layout: { x: 16, y: 0, w: 16, h: 8 },
          },
          {
            id: "result-b",
            type: "display_image",
            label: "Result B",
            output_id: "image_b",
            layout: { x: 0, y: 8, w: 16, h: 8 },
          },
        ],
      },
    ],
  },
};

function mockConfiguredDashboardFetch(
  fetchMock: ReturnType<typeof vi.fn>,
  runtimeResponse = readyRuntime,
) {
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
    if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(runtimeResponse));
    if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
    if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
    if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
    if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
    if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
      return Promise.resolve(
        jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
        }),
      );
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
}

function renderRunPage(props: Partial<ComponentProps<typeof WorkflowRunPage>> = {}) {
  return render(
    <WorkflowRunPage
      workflowId="text_to_image_v0"
      onBack={vi.fn()}
      onNavigate={vi.fn()}
      {...props}
    />,
  );
}

async function waitForReadyStatus() {
  expect((await screen.findAllByText("Ready")).length).toBeGreaterThan(0);
}

function dispatchPointer(target: Window | Node, type: string, init: { pointerId?: number; clientX: number; clientY: number }) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    pointerId: { value: init.pointerId ?? 1 },
    clientX: { value: init.clientX },
    clientY: { value: init.clientY },
  });
  fireEvent(target, event);
}

describe("WorkflowRunPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("validates requirements, starts a run, polls progress, and shows the result", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(
          jsonResponse({
            job_id: "job-1",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-1/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-1",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-1/result")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-1",
            status: "completed",
            outputs: [
              {
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("Result saved by the local workflow.")).toBeInTheDocument();
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output",
    );
  });

  it("blocks the run and explains missing model requirements", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
        return Promise.resolve(jsonResponse(missingModelSummary));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(
          jsonResponse({
            workflow_id: "text_to_image_v0",
            valid: false,
            missing_models: [
              {
                folder: "checkpoints",
                filename: "v1-5-pruned-emaonly-fp16.safetensors",
                source_url: null,
                checksum: null,
              },
            ],
            errors: [],
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByText("This workflow needs required models")).toBeInTheDocument();
    expect(screen.getByText(/v1-5-pruned-emaonly-fp16\.safetensors/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
  });

  it("blocks the run when the local engine is offline", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse({ ...readyRuntime, reachable: false, managed_process_running: false }));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByText("The local AI engine is offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
  });

  it("shows a failed run state", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-2",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-2/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-2",
            status: "failed",
            value: null,
            max: null,
            current_node: "3",
            message: "model failed",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-2/result")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-2",
            status: "failed",
            outputs: [],
            error: "model failed",
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("Workflow failed")).toBeInTheDocument();
    expect(screen.getByText("model failed")).toBeInTheDocument();
  });

  it("shows a memory waiting state without polling a queue id", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "workflow-run-queue-text_to_image_v0-1",
            workflow_id: "text_to_image_v0",
            engine: "noofy",
            status: "queued_pending_memory",
            queue_id: "workflow-run-queue-text_to_image_v0-1",
            message: "This workflow is waiting until the current GPU work finishes.",
            memory_status: {
              state: "waiting_for_gpu",
              message: "This workflow is waiting until the current GPU work finishes.",
              risk_level: "high",
              queue_id: "workflow-run-queue-text_to_image_v0-1",
              can_cancel: true,
              can_retry_after_cleanup: true,
            },
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("Waiting for the GPU")).toBeInTheDocument();
    expect(screen.getAllByText("This workflow is waiting until the current GPU work finishes.").length).toBeGreaterThan(0);
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/api/jobs/workflow-run-queue-text_to_image_v0-1/progress"),
      ),
    ).toBe(false);
  });

  it("shows a blocked memory state", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "blocked-memory-text_to_image_v0",
            workflow_id: "text_to_image_v0",
            engine: "noofy",
            status: "blocked_by_memory",
            message: "This workflow needs more memory than Noofy can safely use right now.",
            memory_status: {
              state: "blocked_by_memory",
              message: "This workflow needs more memory than Noofy can safely use right now.",
              risk_level: "high",
              queue_id: null,
              can_cancel: true,
              can_retry_after_cleanup: false,
            },
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("Not enough memory")).toBeInTheDocument();
    expect(screen.getAllByText("This workflow needs more memory than Noofy can safely use right now.").length).toBeGreaterThan(0);
  });

  it("cancels a running job", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-3",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "running",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-3/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-3",
            status: "running",
            value: 4,
            max: 20,
            current_node: "3",
            message: "Generating image...",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-3/cancel")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-3",
            status: "canceled",
            value: null,
            max: null,
            current_node: null,
            message: "Cancel requested",
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    const topBarProgress = await screen.findByRole("progressbar", { name: /workflow progress/i });
    expect(topBarProgress).toHaveAttribute("aria-valuenow", "20");
    expect(screen.getByText("20%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(await screen.findByText("Run canceled.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("progressbar", { name: /workflow progress/i })).not.toBeInTheDocument();
    });
  });

  it("passes the runtime token to the job event stream URL", async () => {
    const eventSourceMock = vi.fn(function (this: { addEventListener: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> }) {
      this.addEventListener = vi.fn();
      this.close = vi.fn();
    });
    vi.stubGlobal("EventSource", eventSourceMock);
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiToken: "runtime-secret",
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-4",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-4/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-4",
            status: "running",
            value: null,
            max: null,
            current_node: null,
            message: "Preparing workflow...",
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    await waitFor(() => {
      expect(eventSourceMock).toHaveBeenCalledWith("/api/jobs/job-4/events?token=runtime-secret");
    });
  });

  it("renders canvas widgets on the shared builder-style canvas at their configured positions", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    await screen.findByRole("button", { name: /workflow options/i });
    const promptCell = screen.getByRole("textbox").closest("article");
    const resultCell = screen.getByText("Result A").closest("article");

    expect(screen.getByRole("main", { name: "Workflow dashboard canvas" })).toHaveClass("layout-canvas");
    expect(document.querySelector("#canvas-dashboard-surface")).toHaveStyle({ "--layout-columns": "32" });
    expect(promptCell).toHaveClass("layout-canvas-widget", "layout-canvas-widget--run");
    expect(promptCell).toHaveStyle({
      left: "0%",
      top: "0px",
      width: "50%",
      height: "192px",
    });
    expect(resultCell).toHaveStyle({
      left: "50%",
      top: "0px",
      width: "50%",
      height: "256px",
    });
  });

  it("renders canvas mode as a full-workspace canvas without the normal page header or engine notice", async () => {
    mockConfiguredDashboardFetch(fetchMock, { ...readyRuntime, reachable: false, managed_process_running: false });

    renderRunPage();

    expect(await screen.findByRole("button", { name: /workflow options/i })).toBeInTheDocument();
    expect(screen.getByRole("main", { name: "Workflow dashboard canvas" })).toHaveClass("layout-canvas");
    expect(document.querySelector(".main-workspace--canvas-run")).toBeInTheDocument();
    expect(document.querySelector(".workspace-content--canvas-run")).toBeInTheDocument();
    expect(document.querySelector("#canvas-dashboard-surface")).toHaveStyle({
      "--layout-surface-min-height": "768px",
    });
    expect(screen.queryByRole("button", { name: /back to home/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Text to Image" })).not.toBeInTheDocument();
    expect(
      screen.queryByText("Describe the image you want, then let Noofy run the local workflow in the background."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("The local AI engine is offline")).not.toBeInTheDocument();
  });

  it("uses the selected canvas shell while workflow data is loading", () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "canvas" }));
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      return new Promise<Response>(() => {});
    });

    renderRunPage();

    expect(screen.getByRole("main", { name: "Workflow dashboard canvas" })).toHaveClass("layout-canvas");
    expect(document.querySelector(".main-workspace--canvas-run")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Inputs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Preview" })).not.toBeInTheDocument();
  });

  it("shows the compact resource monitor in the top bar while idle", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    expect(await screen.findByLabelText("Resource monitor")).toBeInTheDocument();
    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("23%")).toBeInTheDocument();
    expect(screen.getByText("RAM")).toBeInTheDocument();
    expect(screen.getByText("11 / 32 GB")).toBeInTheDocument();
    expect(screen.getByText("VRAM")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
  });

  it("keeps an imported workflow with no runtime capsule openable but not runnable", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(
          jsonResponse({
            ...workflowStatus,
            install: { status: "unsupported" },
            can_prepare: false,
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(
          jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: "0.1.0",
            values: {},
            layout_overrides: {},
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByText("This workflow cannot run on this machine")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
  });

  it("renders the classic two-panel dashboard when classic mode is selected", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    expect(await screen.findByRole("heading", { name: "Inputs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /workflow options/i })).not.toBeInTheDocument();
  });

  it("shows canvas actions in the top-right options menu and closes it from outside interactions", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    const optionsButton = await screen.findByRole("button", { name: /workflow options/i });
    expect(screen.getByRole("button", { name: /cancel run/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeInTheDocument();
    expect(screen.queryByText("Restore dashboard to the workflow default values")).not.toBeInTheDocument();

    fireEvent.click(optionsButton);

    expect(screen.getByRole("menu", { name: /workflow options/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /export as noofy/i })).toHaveAttribute(
      "href",
      "/api/workflows/text_to_image_v0/export",
    );
    expect(screen.getByRole("menuitem", { name: /export as json/i })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: /edit dashboard layout/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /edit widgets/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /restore dashboard to the workflow default values/i })).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu", { name: /workflow options/i })).not.toBeInTheDocument();

    fireEvent.click(optionsButton);
    expect(screen.getByRole("menu", { name: /workflow options/i })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: /workflow options/i })).not.toBeInTheDocument();
  });

  it("disables input controls while editing the canvas layout", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    await screen.findByRole("button", { name: /workflow options/i });
    const promptInput = screen.getByRole("textbox");
    expect(promptInput).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: /resize prompt/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    expect(screen.queryByRole("menu", { name: /workflow options/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /workflow options/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /cancel run/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run workflow/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Workflow progress")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cancel$/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /resize prompt from/i })).toHaveLength(4);
    expect(screen.getByRole("button", { name: /resize prompt from top-left/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from top-right/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from bottom-left/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resize prompt from bottom-right/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize prompt width/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resize prompt height/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^move prompt$/i })).not.toBeInTheDocument();
    expect(promptInput).toBeDisabled();
  });

  it("cancels an edit layout session without applying the draft resize", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const canvasSurface = document.querySelector("#canvas-dashboard-surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const promptCell = screen.getByRole("textbox").closest("article");
    dispatchPointer(screen.getByRole("button", { name: /resize prompt from bottom-right/i }), "pointerdown", {
      clientX: 600,
      clientY: 192,
    });
    dispatchPointer(window, "pointermove", { clientX: 600, clientY: 256 });
    dispatchPointer(window, "pointerup", { clientX: 600, clientY: 256 });

    expect(promptCell).toHaveStyle({ height: "256px" });

    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(screen.getByRole("textbox")).not.toBeDisabled();
    expect(screen.getByRole("button", { name: /workflow options/i })).toBeInTheDocument();
    expect(promptCell).toHaveStyle({ width: "50%" });
    expect(promptCell).toHaveStyle({ height: "192px" });
  });

  it("saves a grid-snapped resized layout to user state overrides", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const canvasSurface = document.querySelector("#canvas-dashboard-surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    dispatchPointer(screen.getByRole("button", { name: /resize prompt from bottom-right/i }), "pointerdown", {
      clientX: 600,
      clientY: 192,
    });
    dispatchPointer(window, "pointermove", { clientX: 600, clientY: 256 });
    dispatchPointer(window, "pointerup", { clientX: 600, clientY: 256 });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    expect(await screen.findByRole("button", { name: /workflow options/i })).toBeInTheDocument();

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(putCall).toBeDefined();
      const body = JSON.parse((putCall![1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 0, y: 0, w: 16, h: 8 });
    });
  });

  it("moves widgets by snapped grid cells and saves the exact previewed placement", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const canvasSurface = document.querySelector("#canvas-dashboard-surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const promptCell = screen.getByRole("textbox").closest("article")!;
    expect(screen.queryByRole("button", { name: /^move prompt$/i })).not.toBeInTheDocument();
    dispatchPointer(promptCell, "pointerdown", {
      clientX: 300,
      clientY: 96,
    });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 160 });

    expect(promptCell).toHaveClass("layout-canvas-widget--moving");
    expect(promptCell).not.toHaveClass("layout-canvas-widget--preview");
    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "64px" });
    });
    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 160 });

    expect(promptCell).not.toHaveClass("layout-canvas-widget--moving");
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(putCall).toBeDefined();
      const body = JSON.parse((putCall![1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 0, y: 2, w: 16, h: 6 });
    });
  });

  it("moves directly to the snapped grid cell during fast horizontal drags", async () => {
    const packageData = {
      ...configuredPackageData,
      outputs: [],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [
          {
            id: "main",
            title: "Main",
            controls: [
              {
                id: "prompt",
                type: "textarea",
                label: "Prompt",
                input_id: "prompt",
                layout: { x: 4, y: 2, w: 8, h: 6 },
              },
            ],
          },
        ],
      },
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(packageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(
          jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: "0.1.0",
            values: {},
            layout_overrides: {},
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const canvasSurface = document.querySelector("#canvas-dashboard-surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const promptCell = screen.getByRole("textbox").closest("article")!;
    dispatchPointer(promptCell, "pointerdown", { clientX: 250, clientY: 160 });
    dispatchPointer(window, "pointermove", { clientX: 325, clientY: 160 });

    await waitFor(() => {
      expect(promptCell).toHaveStyle({ left: "18.75%" });
    });

    dispatchPointer(window, "pointerup", { clientX: 325, clientY: 160 });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(putCall).toBeDefined();
      const body = JSON.parse((putCall![1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 6, y: 2, w: 8, h: 6 });
    });
  });

  it("lets dragged widgets pass through occupied cells and drops at the nearest free grid cell", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const canvasSurface = document.querySelector("#canvas-dashboard-surface") as HTMLElement;
    vi.spyOn(canvasSurface, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1200,
      bottom: 768,
      width: 1200,
      height: 768,
      toJSON: () => ({}),
    } as DOMRect);

    const promptCell = screen.getByRole("textbox").closest("article")!;
    dispatchPointer(promptCell, "pointerdown", {
      clientX: 300,
      clientY: 96,
    });
    dispatchPointer(window, "pointermove", { clientX: 300, clientY: 224 });

    await waitFor(() => {
      expect(promptCell).toHaveStyle({ top: "128px" });
    });

    const dropPreview = document.querySelector(".layout-canvas-widget--drop-preview") as HTMLElement;
    expect(dropPreview).toBeInTheDocument();
    expect(dropPreview).toHaveStyle({ left: "0%", top: "64px" });

    dispatchPointer(window, "pointerup", { clientX: 300, clientY: 224 });

    expect(promptCell).toHaveStyle({ left: "0%", top: "64px" });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
      expect(putCall).toBeDefined();
      const body = JSON.parse((putCall![1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 0, y: 2, w: 16, h: 6 });
    });
  });

  it("opens the dashboard builder widget step from the canvas options menu", async () => {
    const onEditWidgets = vi.fn();
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage({ onEditWidgets });

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit widgets/i }));

    expect(onEditWidgets).toHaveBeenCalledWith(
      expect.objectContaining({
        workflowId: "text_to_image_v0",
        workflowName: "Text to Image",
        widgets: expect.arrayContaining([
          expect.objectContaining({
            id: "prompt",
            widgetType: "textarea",
            layout: expect.objectContaining({ x: 0, y: 0, w: 16, h: 6 }),
          }),
        ]),
      }),
    );
  });

  it("renders each canvas output widget from its bound result node", async () => {
    const createObjectUrl = vi.fn(() => "blob:noofy-output");
    const revokeObjectUrl = vi.fn();
    let clickedDownload = "";
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clickedDownload = this.download;
    });

    const packageData = {
      metadata: {
        id: "text_to_image_v0",
        name: "Text to Image",
        version: "0.1.0",
        description: "",
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
      outputs: [
        { id: "image_a", label: "Image A", node_id: "9", type: "image" },
        { id: "image_b", label: "Image B", node_id: "10", type: "image" },
      ],
      dashboard: {
        version: "0.1.0",
        status: "configured",
        sections: [
          {
            id: "main",
            title: "Main",
            controls: [
              {
                id: "prompt",
                type: "textarea",
                label: "Prompt",
                input_id: "prompt",
                layout: { x: 0, y: 0, w: 16, h: 6 },
              },
              {
                id: "result-a",
                type: "display_image",
                label: "Result A",
                output_id: "image_a",
                show_download: true,
                layout: { x: 16, y: 0, w: 16, h: 8 },
              },
              {
                id: "result-b",
                type: "display_image",
                label: "Result B",
                output_id: "image_b",
                layout: { x: 0, y: 8, w: 16, h: 8 },
              },
            ],
          },
        ],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(packageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(
          jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: "0.1.0",
            values: {},
            layout_overrides: {},
          }),
        );
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(
          jsonResponse({
            job_id: "job-canvas",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-canvas/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-canvas",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-canvas/result")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-canvas",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-canvas/outputs/view?filename=node-9-a.png&subfolder=&type=output",
                    },
                    {
                      view_url:
                        "/api/jobs/job-canvas/outputs/view?filename=node-9-b.png&subfolder=&type=output",
                    },
                  ],
                },
              },
              {
                node_id: "10",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-canvas/outputs/view?filename=node-10.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          }),
        );
      }

      if (url.endsWith("/api/jobs/job-canvas/outputs/view?filename=node-9-a.png&subfolder=&type=output")) {
        return Promise.resolve(new Response(new Blob(["image"], { type: "image/png" }), { status: 200 }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByText("Result A")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByAltText("Generated workflow output 1")).toHaveAttribute(
      "src",
      "/api/jobs/job-canvas/outputs/view?filename=node-9-a.png&subfolder=&type=output",
    );
    expect(screen.getByAltText("Generated workflow output 2")).toHaveAttribute(
      "src",
      "/api/jobs/job-canvas/outputs/view?filename=node-9-b.png&subfolder=&type=output",
    );
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-canvas/outputs/view?filename=node-10.png&subfolder=&type=output",
    );

    fireEvent.click(screen.getByRole("button", { name: /download image/i }));

    await waitFor(() => expect(createObjectUrl).toHaveBeenCalled());
    expect(clickedDownload).toBe("node-9-a.png");
    await waitFor(() => expect(revokeObjectUrl).toHaveBeenCalledWith("blob:noofy-output"));
  });
});
