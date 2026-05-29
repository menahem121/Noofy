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
  description: "Generate a new image from a simple text prompt.",
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

  beforeEach(() => {
    lastOpened = null;
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(runtime));
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse({ cpu: null, ram: null, vram: null }));
      if (url.endsWith("/api/workflows")) return Promise.resolve(jsonResponse([{ ...workflowSummary, last_opened: lastOpened }]));
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
        return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", status: "no_runner", lease_id: null, runner: null }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-1", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-1/progress")) {
        return Promise.resolve(jsonResponse({ job_id: "job-1", status: "running", value: null, max: null, current_node: null, message: "Generating..." }));
      }
      if (url.endsWith("/api/jobs/job-1/cancel") && method === "POST") {
        return Promise.resolve(jsonResponse({ job_id: "job-1", status: "canceled", value: null, max: null, current_node: null, message: "Canceled." }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
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
    expect(await screen.findByRole("button", { name: "Run Workflow" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close Other Workflow workspace tab" }));

    expect(screen.getByRole("button", { name: "Run Workflow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Text to Image" })).toHaveAttribute("aria-current", "page");
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Other Workflow" })).not.toBeInTheDocument();
    });
  });

  it("confirms before closing a tab with active workflow work and cancels only after confirmation", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Open Text to Image" }));
    fireEvent.click(await screen.findByRole("button", { name: "Run Workflow" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/jobs/job-1/progress"))).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    expect(await screen.findByRole("dialog", { name: "Stop this workflow?" })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/jobs/job-1/cancel"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Stop this workflow?" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close Text to Image workspace tab" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close Text to Image workspace tab" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop and close" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/jobs/job-1/cancel"))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Close Text to Image workspace tab" })).not.toBeInTheDocument();
    });
  });
});
