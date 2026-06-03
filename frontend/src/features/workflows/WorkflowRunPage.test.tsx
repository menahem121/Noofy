import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimeStatusProvider, type RuntimeHealthState } from "../app/RuntimeStatusProvider";
import { splitDiagnosticLogs, WorkflowRunPage } from "./WorkflowRunPage";

const canvasCss = readFileSync(resolve(process.cwd(), "src/styles/canvas.css"), "utf8");

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

const readyRuntimeState: Partial<RuntimeHealthState> = {
  backendStatus: "reachable",
  engineStatus: "ready",
  runtime: readyRuntime as RuntimeHealthState["runtime"],
  hasKnownState: true,
  lastCheckedAt: Date.now(),
};

function diagnosticEvent(source: string, message: string, details: Record<string, unknown> = {}) {
  return {
    id: Math.floor(Math.random() * 100000),
    timestamp: "2026-05-08T10:00:00+00:00",
    level: source.includes("error") ? "error" : "info",
    message,
    source,
    job_id: null,
    workflow_id: "text_to_image_v0",
    details,
  };
}

const engineOfflineRuntimeState: Partial<RuntimeHealthState> = {
  ...readyRuntimeState,
  engineStatus: "offline",
  runtime: {
    ...readyRuntime,
    reachable: false,
    managed_process_running: false,
  } as RuntimeHealthState["runtime"],
};

const engineStartingRuntimeState: Partial<RuntimeHealthState> = {
  ...readyRuntimeState,
  engineStatus: "starting",
  runtime: {
    ...readyRuntime,
    reachable: false,
    managed_process_running: true,
    sidecar_starting: true,
  } as RuntimeHealthState["runtime"],
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

const configuredApiSettings = {
  providers: {
    hugging_face: { provider: "hugging_face", label: "Hugging Face", configured: false, last_four: null },
    civitai: { provider: "civitai", label: "CivitAI", configured: true, last_four: "1234" },
    comfy_org: { provider: "comfy_org", label: "ComfyUI Account API Key", configured: false, last_four: null },
  },
  credential_store: { available: true, status: "available", error: null },
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

const unconfiguredPackageData = {
  ...configuredPackageData,
  dashboard: {
    version: "0.1.0",
    status: "not_configured",
    sections: [],
  },
};

function dashboardOnlyNotePackageData() {
  return {
    ...configuredPackageData,
    inputs: [],
    outputs: [],
    dashboard: {
      ...configuredPackageData.dashboard,
      sections: [
        {
          id: "main",
          title: "Main",
          controls: [
            {
              id: "creator-note",
              type: "note",
              label: "Before you run",
              description: "Use a square source image.\nLarge images take longer.",
              layout: { x: 0, y: 0, w: 6, h: 4, min_w: 6, min_h: 4 },
            },
          ],
        },
      ],
    },
  };
}

const loraPackageData = {
  metadata: {
    id: "text_to_image_v0",
    name: "Text to Image",
    version: "0.1.0",
    description: "",
  },
  required_models: [
    {
      folder: "checkpoints",
      filename: "sdxl-base.safetensors",
      node_id: "4",
      node_type: "CheckpointLoaderSimple",
      input_name: "ckpt_name",
      checksum: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      model_type: "checkpoint",
      size_bytes: 12,
    },
  ],
  comfyui_graph: {
    "4": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sdxl-base.safetensors" } },
    "12": { class_type: "LoraLoader", inputs: { model: ["4", 0], clip: ["4", 1], lora_name: "None" } },
  },
  inputs: [
    {
      id: "style_lora",
      label: "Style LoRA",
      control: "lora_loader",
      binding: { node_id: "12", input_name: "lora_name" },
      default: "None",
      validation: { options: ["None", "existing.safetensors"] },
    },
  ],
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
            id: "style_lora",
            type: "lora_loader",
            label: "Style LoRA",
            input_id: "style_lora",
            layout: { x: 0, y: 0, w: 12, h: 4 },
          },
        ],
      },
    ],
  },
};

const loraMissingSummary = {
  workflow_id: "text_to_image_v0",
  total_count: 1,
  available_count: 0,
  possible_match_count: 0,
  missing_count: 1,
  needs_manual_download_count: 0,
  ready_to_run: false,
  models: [
    {
      requirement_id: "style_lora",
      node_id: "12",
      node_type: "LoraLoader",
      input_name: "lora_name",
      filename: "missing-style.safetensors",
      model_type: "lora",
      folder: "loras",
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

const loraPackageWithMissingRequiredModel = {
  ...loraPackageData,
  inputs: loraPackageData.inputs.map((input) =>
    input.id === "style_lora" ? { ...input, default: "" } : input,
  ),
  required_models: [
    ...(loraPackageData.required_models ?? []),
    {
      folder: "loras",
      filename: "missing-style.safetensors",
      node_id: "12",
      node_type: "LoraLoader",
      input_name: "lora_name",
      checksum: null,
      model_type: "lora",
      size_bytes: null,
    },
  ],
};

function mockConfiguredDashboardFetch(
  fetchMock: ReturnType<typeof vi.fn>,
  runtimeResponse: unknown | (() => unknown) = readyRuntime,
  packageData: unknown = configuredPackageData,
  runResponse: unknown | ((init?: RequestInit) => unknown) | null = null,
  extraHandler: ((url: string, init?: RequestInit) => Response | Promise<Response> | undefined) | null = null,
) {
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const extraResponse = extraHandler?.(url, init);
    if (extraResponse) return Promise.resolve(extraResponse);
    if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
    if (url.endsWith("/api/runtime")) {
      const response = typeof runtimeResponse === "function" ? runtimeResponse() : runtimeResponse;
      return Promise.resolve(jsonResponse(response));
    }
    if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
    if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
    if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(packageData));
    if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
    if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
    if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
      return Promise.resolve(jsonResponse({ active_count: 0, queued_count: 0, total_count: 0 }));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/export") && init?.method === "POST") {
      return Promise.resolve(new Response(new Uint8Array([110, 111, 111, 102, 121])));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/export/comfyui-json") && init?.method === "POST") {
      return Promise.resolve(new Response(JSON.stringify({ workflow: true })));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/dashboard") && init?.method === "DELETE") {
      return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", removed: true }));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/dashboard") && init?.method === "PUT") {
      return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", status: "configured", valid: true }));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/run") && runResponse !== null) {
      const response = typeof runResponse === "function" ? runResponse(init) : runResponse;
      return Promise.resolve(jsonResponse(response));
    }
    if (url.endsWith("/api/workflows/text_to_image_v0/user-state/values") && init?.method === "DELETE") {
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
    if (url.endsWith("/api/workflows/text_to_image_v0/user-state/layout") && init?.method === "DELETE") {
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

function renderRunPage(
  props: Partial<ComponentProps<typeof WorkflowRunPage>> = {},
  runtimeState: Partial<RuntimeHealthState> = readyRuntimeState,
) {
  return render(
    <RuntimeStatusProvider initialRuntimeState={runtimeState} skipInitialRefresh>
      <WorkflowRunPage
        workflowId="text_to_image_v0"
        onBack={vi.fn()}
        onNavigate={vi.fn()}
        {...props}
      />
    </RuntimeStatusProvider>,
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

function mockCanvasActionBarBounds({
  left,
  top,
  width = 340,
  height = 40,
}: {
  left: number;
  top: number;
  width?: number;
  height?: number;
}) {
  const frame = screen.getByRole("main", { name: /workflow dashboard canvas/i });
  vi.spyOn(frame, "getBoundingClientRect").mockReturnValue({
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
  const actionBar = document.querySelector(".canvas-action-cluster") as HTMLElement;
  vi.spyOn(actionBar, "getBoundingClientRect").mockReturnValue({
    x: left,
    y: top,
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect);
}

function civitaiSearchResponse() {
  return {
    status: "ok",
    user_facing_message: "LoRAs matching this base model.",
    base_model_filter: "SDXL 1.0",
    used_server_base_model_filter: true,
    detection: {
      status: "detected",
      base_model: "SDXL 1.0",
      confidence: "high",
      label: "sdxl-base.safetensors",
      message: "LoRAs matching this base model.",
      candidates: [],
      available_base_models: ["SD 1.5", "SDXL 1.0", "Pony", "Flux.1 D"],
    },
    items: [
      {
        model_id: 100,
        model_version_id: 200,
        file_id: 300,
        name: "Cinematic SDXL LoRA",
        creator: "maker",
        version_name: "v1",
        base_model: "SDXL 1.0",
        file_name: "cinematic.safetensors",
        file_size_bytes: 1024,
        download_count: 1200,
        thumbs_up_count: 80,
        rating_count: 80,
        trigger_words: ["cinematic"],
        preview_image_url: null,
        model_page_url: "https://civitai.com/models/100?modelVersionId=200",
        already_downloaded: false,
      },
    ],
    next_cursor: null,
  };
}

describe("WorkflowRunPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("splits diagnostics into ComfyUI engine logs and Noofy logs from existing sources", () => {
    const { comfyuiLogs, noofyLogs } = splitDiagnosticLogs([
      diagnosticEvent("comfyui.adapter", "ComfyUI execution failed"),
      diagnosticEvent("comfyui.stdout", "model load failed"),
      diagnosticEvent("runtime.manager", "Managed ComfyUI crashed"),
      diagnosticEvent("runtime.runner_process.stdout", "runner traceback"),
      diagnosticEvent("future.engine", "runner failed", { runner_id: "runner-1" }),
      diagnosticEvent("engine.service", "Submitting workflow run", { runner_id: "runner-1" }),
      diagnosticEvent("memory_governor", "Started memory sampling"),
      diagnosticEvent("workflow.models", "Model validation failed"),
    ]);

    expect(comfyuiLogs.map((event) => event.source)).toEqual([
      "comfyui.adapter",
      "comfyui.stdout",
      "runtime.manager",
      "runtime.runner_process.stdout",
      "future.engine",
    ]);
    expect(noofyLogs.map((event) => event.source)).toEqual([
      "engine.service",
      "memory_governor",
      "workflow.models",
    ]);
  });

  it("resumes dashboard setup instead of rendering run views when the package dashboard is not ready", async () => {
    const onConfigureDashboard = vi.fn();
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, unconfiguredPackageData);

    renderRunPage({ onConfigureDashboard });

    expect(await screen.findByRole("heading", { name: "Finish Text to Image" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run workflow/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("main", { name: /workflow dashboard canvas/i })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(onConfigureDashboard).toHaveBeenCalledWith("text_to_image_v0", "Text to Image");
    });
  });

  it("opens the CivitAI LoRA modal and searches through the Noofy backend only", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(loraPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
        }));
      }
      if (url.endsWith("/api/model-sources/civitai/search-loras")) {
        const body = JSON.parse(String(init?.body));
        expect(body.workflow_id).toBe("text_to_image_v0");
        expect(body.lora_input_id).toBe("style_lora");
        return Promise.resolve(jsonResponse(civitaiSearchResponse()));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();
    await screen.findByRole("button", { name: /Download more LoRAs/i });
    fireEvent.click(screen.getByRole("button", { name: /Download more LoRAs/i }));

    expect(await screen.findByText("Cinematic SDXL LoRA")).toBeInTheDocument();
    expect(screen.getByText("Base model: SDXL 1.0")).toBeInTheDocument();
    expect(screen.getByText("cinematic")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("civitai.com"))).toBe(false);
  });

  it("refreshes LoRA options and auto-selects the downloaded LoRA when the value is unchanged", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(loraPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
        }));
      }
      if (url.endsWith("/api/model-sources/civitai/search-loras")) {
        return Promise.resolve(jsonResponse(civitaiSearchResponse()));
      }
      if (url.endsWith("/api/model-sources/civitai/download")) {
        const body = JSON.parse(String(init?.body));
        expect(body.observed_lora_value).toBe("None");
        return Promise.resolve(jsonResponse({
          job_id: "job-1",
          status: "queued",
          user_facing_message: "CivitAI LoRA download is queued.",
          target_filename: "cinematic.safetensors",
          model_key: "loras/cinematic.safetensors",
          observed_lora_value: "None",
        }));
      }
      if (url.endsWith("/api/models/downloads/job-1")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-1",
          status: "completed",
          user_facing_message: "Model download check finished.",
          current_model_filename: "cinematic.safetensors",
          current_model_index: 1,
          total_models: 1,
          bytes_downloaded: 1024,
          total_bytes: 1024,
          percent: 100,
          speed_bytes_per_second: null,
          models: [],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();
    fireEvent.click(await screen.findByRole("button", { name: /Download more LoRAs/i }));
    await screen.findByText("Cinematic SDXL LoRA");
    fireEvent.click(screen.getByRole("button", { name: /^Download$/i }));

    await waitFor(() => {
      expect(screen.getByDisplayValue("cinematic.safetensors")).toBeInTheDocument();
    }, { timeout: 2500 });
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/workflows/text_to_image_v0/package"))).toBe(true);
  });

  it("does not overwrite the LoRA value if the user changes it before download completes", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(loraPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
        }));
      }
      if (url.endsWith("/api/model-sources/civitai/search-loras")) {
        return Promise.resolve(jsonResponse(civitaiSearchResponse()));
      }
      if (url.endsWith("/api/model-sources/civitai/download")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-2",
          status: "queued",
          user_facing_message: "CivitAI LoRA download is queued.",
          target_filename: "cinematic.safetensors",
          model_key: "loras/cinematic.safetensors",
          observed_lora_value: "None",
        }));
      }
      if (url.endsWith("/api/models/downloads/job-2")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-2",
          status: "completed",
          user_facing_message: "Model download check finished.",
          current_model_filename: "cinematic.safetensors",
          current_model_index: 1,
          total_models: 1,
          bytes_downloaded: 1024,
          total_bytes: 1024,
          percent: 100,
          speed_bytes_per_second: null,
          models: [],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();
    fireEvent.click(await screen.findByRole("button", { name: /Download more LoRAs/i }));
    await screen.findByText("Cinematic SDXL LoRA");
    fireEvent.click(screen.getByRole("button", { name: /^Download$/i }));
    fireEvent.change(screen.getByDisplayValue("None"), { target: { value: "existing.safetensors" } });

    await waitFor(() => {
      expect(screen.getByDisplayValue("existing.safetensors")).toBeInTheDocument();
    }, { timeout: 2500 });
    expect(screen.queryByDisplayValue("cinematic.safetensors")).not.toBeInTheDocument();
  });

  it("does not prompt for a missing LoRA model while None is selected", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(loraPackageWithMissingRequiredModel));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(loraMissingSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse({
          workflow_id: "text_to_image_v0",
          valid: false,
          missing_models: [
            {
              folder: "loras",
              filename: "missing-style.safetensors",
              source_url: null,
              checksum: null,
              model_type: "lora",
            },
          ],
          errors: [],
        }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        const body = JSON.parse(String(init?.body));
        expect(body.inputs.style_lora).toBe("None");
        return Promise.resolve(jsonResponse({
          job_id: "job-lora-none",
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "blocked_by_memory",
        }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: "0.1.0",
          values: {},
          layout_overrides: {},
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByDisplayValue("None")).toBeInTheDocument();
    expect(screen.queryByText("This workflow needs required models")).not.toBeInTheDocument();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/workflows/text_to_image_v0/run"))).toBe(true);
    });
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
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByText("Result saved by the local workflow.")).toBeInTheDocument();
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output",
    );
  });

  it("shows optimistic run feedback immediately while submission is pending", async () => {
    const runRequest = deferred<Response>();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") return runRequest.promise;
      if (url.endsWith("/api/jobs/job-pending/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-pending",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-pending/result")) {
        return Promise.resolve(jsonResponse({ job_id: "job-pending", status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    const topBarProgress = await screen.findByRole("progressbar", { name: /workflow progress/i });
    expect(topBarProgress).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("Starting workflow...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^cancel$/i })).toBeDisabled();
    expect(document.querySelector(".primary-button .spin")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-pending",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result saved by the local workflow.")).toBeInTheDocument();
  });

  it("shows preparation progress while Run prepares a custom-node workflow and resumes after the job starts", async () => {
    const runRequest = deferred<Response>();
    const preparingStatus = {
      ...workflowStatus,
      workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
      install: {
        status: "resolving_dependencies",
        user_facing_message: "Resolving custom-node dependencies.",
        smoke_test_report: {
          dependency_env: { status: "not_run", message: null, details: {} },
          custom_node_import: { status: "not_run", message: null, details: {} },
          runner_health: { status: "not_run", message: null, details: {} },
          workflow_execution: { status: "not_run", message: null, details: {} },
        },
      },
    };
    const smokeTestingStatus = {
      ...preparingStatus,
      install: {
        status: "smoke_testing",
        user_facing_message: "Checking the isolated runner.",
        smoke_test_report: {
          dependency_env: { status: "passed", message: "Dependency imports passed.", details: {} },
          custom_node_import: { status: "passed", message: "Custom node registration passed.", details: {} },
          runner_health: { status: "not_run", message: null, details: {} },
          workflow_execution: { status: "not_run", message: null, details: {} },
        },
      },
    };
    let statusCalls = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        statusCalls += 1;
        return Promise.resolve(jsonResponse(statusCalls > 1 ? smokeTestingStatus : preparingStatus));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") return runRequest.promise;
      if (url.endsWith("/api/jobs/job-prepared/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-prepared",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-prepared/result")) {
        return Promise.resolve(jsonResponse({ job_id: "job-prepared", status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByRole("dialog", { name: "Preparing workflow" })).toBeInTheDocument();
    expect(screen.getByText(/Resolving custom-node dependencies|Checking the isolated runner/)).toBeInTheDocument();
    expect(screen.getByText("Prepare dependency environment")).toBeInTheDocument();
    expect(screen.getByText("Stage custom-node files")).toBeInTheDocument();
    expect(screen.getByText("Start isolated runner")).toBeInTheDocument();
    expect(screen.getByText("Check custom-node registration")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-prepared",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result saved by the local workflow.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Preparing workflow" })).not.toBeInTheDocument();
    });
  });

  it("does not flash the preparation dialog when a custom-node workflow is already ready", async () => {
    const runRequest = deferred<Response>();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(
          jsonResponse({
            ...workflowStatus,
            workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
            install: { status: "ready", user_facing_message: "Ready" },
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") return runRequest.promise;
      if (url.endsWith("/api/jobs/job-ready/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-ready",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-ready/result")) {
        return Promise.resolve(jsonResponse({ job_id: "job-ready", status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(screen.queryByRole("dialog", { name: "Preparing workflow" })).not.toBeInTheDocument();
    expect(screen.getByText("Starting workflow...")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-ready",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Preparing workflow" })).not.toBeInTheDocument();
    });
  });

  it("shows the root preparation blocker when Run returns a validation failure", async () => {
    const rootCause = "Node package dependency could not be resolved.";
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(
          jsonResponse({
            ...workflowStatus,
            workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
            install: {
              status: "pending",
              user_facing_message: "Not started",
            },
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(
          jsonResponse({
            workflow_id: "text_to_image_v0",
            valid: false,
            missing_models: [],
            errors: [rootCause],
          }),
        );
      }
      if (url.endsWith("/api/logs?limit=200")) {
        return Promise.resolve(
          jsonResponse({
            events: [diagnosticEvent("runtime.workspace", rootCause)],
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByRole("dialog", { name: "Workflow failed" })).toBeInTheDocument();
    expect(screen.getAllByText(rootCause).length).toBeGreaterThan(0);
    expect(screen.getByText(/runtime\.workspace/)).toBeInTheDocument();
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

      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(jsonResponse(workflowStatus));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse(validWorkflow));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage({}, engineOfflineRuntimeState);

    expect(await screen.findByText("The local ComfyUI engine is not reachable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
  });

  it("updates engine status and run gating while the workflow page stays open", async () => {
    vi.useFakeTimers();
    let runtimeCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, () => {
      runtimeCalls += 1;
      return runtimeCalls === 1 ? engineStartingRuntimeState.runtime : readyRuntime;
    });

    render(
      <RuntimeStatusProvider initialRuntimeState={engineStartingRuntimeState}>
        <WorkflowRunPage workflowId="text_to_image_v0" onBack={vi.fn()} onNavigate={vi.fn()} />
      </RuntimeStatusProvider>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getAllByText("Starting").length).toBeGreaterThan(0);
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    expect(runButton).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(runButton).toBeEnabled();
    expect(screen.queryByText("The local ComfyUI engine is starting")).not.toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
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

      if (url.endsWith("/api/jobs/job-2/logs?limit=200")) {
        return Promise.resolve(
          jsonResponse({
            events: [
              { ...diagnosticEvent("comfyui.adapter", "ComfyUI execution failed"), job_id: "job-2" },
              { ...diagnosticEvent("runtime.runner_process.stdout", "Traceback from custom node"), job_id: "job-2" },
              { ...diagnosticEvent("memory_governor", "Started best-effort job memory sampling"), job_id: "job-2" },
            ],
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect((await screen.findAllByText("Workflow failed")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("model failed").length).toBeGreaterThan(0);
    expect(await screen.findByRole("dialog", { name: "Workflow failed" })).toBeInTheDocument();
    expect(screen.getByText(/ComfyUI execution failed/)).toBeInTheDocument();
    expect(screen.getByText(/Started best-effort job memory sampling/)).toBeInTheDocument();
  });

  it("shows a memory waiting state and tracks the queue id", async () => {
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

      if (url.endsWith("/api/jobs/workflow-run-queue-text_to_image_v0-1/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "workflow-run-queue-text_to_image_v0-1",
            queue_id: "workflow-run-queue-text_to_image_v0-1",
            status: "queued_pending_memory",
            value: null,
            max: null,
            current_node: null,
            message: "This workflow is waiting until the current GPU work finishes.",
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
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/jobs/workflow-run-queue-text_to_image_v0-1/progress"),
        ),
      ).toBe(true);
    });
  });

  it("does not show generic memory monitoring copy as a warning", async () => {
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, {
      job_id: "memory-monitoring",
      workflow_id: "text_to_image_v0",
      engine: "noofy",
      status: "running",
      message: "Noofy will try this workflow and watch memory closely.",
      memory_decision: {
        action: "allow_with_monitoring",
        reason_code: "monitoring_only",
      },
      memory_status: {
        state: "monitoring_memory",
        message: "Noofy will try this workflow and watch memory closely.",
        risk_level: "medium",
        queue_id: null,
        can_cancel: true,
        can_retry_after_cleanup: false,
      },
    }, (url) => {
      if (!url.endsWith("/api/jobs/memory-monitoring/progress")) return undefined;
      return jsonResponse({
        job_id: "memory-monitoring",
        status: "running",
        value: null,
        max: null,
        current_node: null,
        message: "Generating image...",
      });
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/workflows/text_to_image_v0/run"))).toBe(true);
    });
    expect(screen.queryByText("Memory status")).not.toBeInTheDocument();
    expect(screen.queryByText("Noofy will try this workflow and watch memory closely.")).not.toBeInTheDocument();
    expect(screen.queryByText("Developer details")).not.toBeInTheDocument();
  });

  it.each([
    {
      state: "waiting_for_active_workflow",
      status: "queued_pending_memory",
      title: "Waiting for another run",
      message: "Noofy will start this workflow after the active run finishes.",
    },
    {
      state: "freeing_previous_models",
      status: "queued_pending_memory",
      title: "Freeing previous models",
      message: "Noofy is unloading idle models so this workflow has enough room to start.",
    },
    {
      state: "unloading_previous_workflow",
      status: "queued_pending_memory",
      title: "Unloading previous workflow",
      message: "Noofy is clearing the previous workflow before starting this one.",
    },
    {
      state: "retrying_after_memory_cleanup",
      status: "queued_pending_memory",
      title: "Retrying after memory cleanup",
      message: "Noofy freed memory and is trying this workflow one more time.",
    },
    {
      state: "memory_cleanup_failed",
      status: "blocked_by_memory",
      title: "Memory cleanup did not finish",
      message: "Noofy tried to free memory, but could not confirm that enough was released.",
    },
    {
      state: "blocked_external_pressure",
      status: "blocked_by_memory",
      title: "Other GPU work is using memory",
      message: "Another process is using GPU memory that Noofy cannot reclaim.",
    },
    {
      state: "blocked_exceeds_capacity",
      status: "blocked_by_memory",
      title: "Workflow exceeds this machine's memory",
      message: "This workflow appears to need more RAM or VRAM than this machine can safely provide.",
    },
    {
      state: "blocked_unattributed_pressure",
      status: "blocked_by_memory",
      title: "Memory is in use but not reclaimable",
      message: "Noofy sees memory pressure, but cannot safely attribute enough of it to memory it owns.",
    },
  ])("shows distinct memory copy for $state", async ({ state, status, title, message }) => {
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, {
      job_id: `memory-${state}`,
      workflow_id: "text_to_image_v0",
      engine: "noofy",
      status,
      message: "Not enough memory is available for this run.",
      memory_decision: {
        action: status === "blocked_by_memory" ? "blocked_by_memory" : "queue_pending_memory",
        reason_code: "test_memory_state",
      },
      memory_status: {
        state,
        message: "Not enough memory is available for this run.",
        risk_level: "high",
        queue_id: status === "queued_pending_memory" ? `memory-${state}` : null,
        can_cancel: status === "queued_pending_memory",
        can_retry_after_cleanup: state === "retrying_after_memory_cleanup",
      },
    }, (url) => {
      if (status !== "queued_pending_memory" || !url.endsWith(`/api/jobs/memory-${state}/progress`)) return undefined;
      return jsonResponse({
        job_id: `memory-${state}`,
        queue_id: `memory-${state}`,
        status,
        value: null,
        max: null,
        current_node: null,
        message,
      });
    });

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
    expect(screen.queryByText("Not enough memory")).not.toBeInTheDocument();
    expect(screen.getByText("Developer details")).toBeInTheDocument();
    expect(screen.getByText(/test_memory_state/)).toBeInTheDocument();
  });

  it.each(["ready_warm_co_resident", "ready_reusing_runner"])(
    "shows a compact models-loaded pill for warm memory state %s",
    async (memoryState) => {
      mockConfiguredDashboardFetch(
        fetchMock,
        readyRuntime,
        configuredPackageData,
        {
          job_id: `warm-${memoryState}`,
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "running",
          message: "Runner is ready.",
          memory_decision: {
            action: "reuse_runner",
            reason_code: "warm_runner",
          },
          memory_status: {
            state: memoryState,
            message: "Runner is ready.",
            risk_level: "low",
            queue_id: null,
            can_cancel: true,
            can_retry_after_cleanup: false,
          },
        },
        (url) => {
          if (!url.endsWith(`/api/jobs/warm-${memoryState}/progress`)) return undefined;
          return jsonResponse({
            job_id: `warm-${memoryState}`,
            status: "running",
            value: null,
            max: null,
            current_node: null,
            message: "Generating image...",
          });
        },
      );

      renderRunPage();

      await waitForReadyStatus();
      fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

      expect(await screen.findByText("Models loaded")).toBeInTheDocument();
      expect(screen.queryByText("Ready to relaunch")).not.toBeInTheDocument();
      expect(screen.queryByText("Developer details")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    },
  );

  it("clears queued memory copy after cancellation", async () => {
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      {
        job_id: "memory-cancel",
        workflow_id: "text_to_image_v0",
        engine: "noofy",
        status: "queued_pending_memory",
        message: "Not enough memory is available for this run.",
        memory_status: {
          state: "freeing_previous_models",
          message: "Not enough memory is available for this run.",
          risk_level: "high",
          queue_id: "memory-cancel",
          can_cancel: true,
          can_retry_after_cleanup: false,
        },
      },
      (url, init) => {
        if (url.endsWith("/api/jobs/memory-cancel/progress")) {
          return jsonResponse({
            job_id: "memory-cancel",
            queue_id: "memory-cancel",
            status: "queued_pending_memory",
            value: null,
            max: null,
            current_node: null,
            message: "Not enough memory is available for this run.",
          });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
          return jsonResponse({ active_count: 0, queued_count: 1, total_count: 1 });
        }
        if (!url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") || init?.method !== "POST") {
          return undefined;
        }
        return jsonResponse({
          canceled_active_count: 0,
          canceled_queued_count: 1,
          already_terminal_count: 0,
          failed_to_cancel_count: 0,
        });
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByText("Freeing previous models")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));

    await waitFor(() => expect(screen.queryByText("Freeing previous models")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
  });

  it("cancels a running job", async () => {
    let cancelCalls = 0;
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

      if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
        return Promise.resolve(jsonResponse({ active_count: 1, queued_count: 0, total_count: 1 }));
      }

      if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued")) {
        cancelCalls += 1;
        return Promise.resolve(
          jsonResponse({
            canceled_active_count: 1,
            canceled_queued_count: 0,
            already_terminal_count: 0,
            failed_to_cancel_count: 0,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    const topBarProgress = await screen.findByRole("progressbar", { name: /workflow progress/i });
    await waitFor(() => {
      expect(topBarProgress).toHaveAttribute("aria-valuenow", "20");
      expect(screen.getByText("20%")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await waitFor(() => expect(cancelCalls).toBe(1));
    await waitFor(() => {
      expect(screen.queryByRole("progressbar", { name: /workflow progress/i })).not.toBeInTheDocument();
    });
  });

  it("submits Batch Count runs with identical captured payloads and no seed mutation", async () => {
    const seededPackageData = {
      ...configuredPackageData,
      inputs: [
        ...configuredPackageData.inputs,
        {
          id: "seed",
          label: "Seed",
          control: "seed_widget",
          binding: { node_id: "7", input_name: "seed" },
          default: 123,
          validation: {},
        },
      ],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: configuredPackageData.dashboard.sections.map((section) => ({
          ...section,
          controls: [
            ...section.controls,
            {
              id: "seed",
              type: "seed_widget",
              label: "Seed",
              input_id: "seed",
              layout: { x: 16, y: 8, w: 8, h: 4 },
            },
          ],
        })),
      },
    };
    const runBodies: Array<{ inputs: Record<string, unknown>; output_preferences_snapshot: unknown }> = [];
    let runIndex = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      seededPackageData,
      (init?: RequestInit) => {
        runIndex += 1;
        runBodies.push(JSON.parse(String(init?.body)));
        return {
          job_id: `job-batch-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        const match = url.match(/\/api\/jobs\/(job-batch-\d+)\/progress$/);
        if (!match) return undefined;
        return jsonResponse({
          job_id: match[1],
          status: "queued",
          value: null,
          max: null,
          current_node: null,
          message: "Queued.",
        });
      },
    );

    renderRunPage();

    const textboxes = await screen.findAllByRole("textbox");
    fireEvent.change(textboxes[0], { target: { value: "batch prompt" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch count" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    await waitFor(() => expect(runBodies).toHaveLength(4));
    const firstBody = runBodies[0];
    expect(firstBody.inputs.prompt).toBe("batch prompt");
    expect(firstBody.inputs.seed).toBe(123);
    expect(runBodies.map((body) => body.inputs)).toEqual([
      firstBody.inputs,
      firstBody.inputs,
      firstBody.inputs,
      firstBody.inputs,
    ]);
    expect(runBodies.map((body) => body.output_preferences_snapshot)).toEqual([
      firstBody.output_preferences_snapshot,
      firstBody.output_preferences_snapshot,
      firstBody.output_preferences_snapshot,
      firstBody.output_preferences_snapshot,
    ]);
  });

  it("keeps initial polling bounded for large batches", async () => {
    let runIndex = 0;
    const progressCalls: string[] = [];
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      () => {
        runIndex += 1;
        return {
          job_id: `job-poll-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        const match = url.match(/\/api\/jobs\/(job-poll-\d+)\/progress$/);
        if (!match) return undefined;
        progressCalls.push(match[1]);
        return jsonResponse({
          job_id: match[1],
          status: "queued",
          value: null,
          max: null,
          current_node: null,
          message: "Queued.",
        });
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch count" }), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    await waitFor(() => expect(runIndex).toBe(20));
    await waitFor(() => expect(progressCalls.length).toBeGreaterThan(0));
    expect(progressCalls.length).toBeLessThanOrEqual(8);
  });

  it("shows active plus queued count, exposes Stop, and confirms workflow-scoped cancel-all", async () => {
    let runIndex = 0;
    let cancelCalls = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      () => {
        runIndex += 1;
        return {
          job_id: `job-queue-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url, init) => {
        const progressMatch = url.match(/\/api\/jobs\/(job-queue-\d+)\/progress$/);
        if (progressMatch) {
          return jsonResponse({
            job_id: progressMatch[1],
            status: "queued",
            value: null,
            max: null,
            current_node: null,
            message: "Queued.",
          });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
          return jsonResponse({ active_count: 1, queued_count: 2, total_count: 3 });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") && init?.method === "POST") {
          cancelCalls += 1;
          return jsonResponse({
            canceled_active_count: 1,
            canceled_queued_count: 2,
            already_terminal_count: 0,
            failed_to_cancel_count: 0,
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch count" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByTitle("3 runs remaining")).toBeInTheDocument();
    const stopButton = screen.getByRole("button", {
      name: "Cancel current run and all queued runs for this workflow",
    });
    fireEvent.click(stopButton);

    expect(await screen.findByRole("dialog", { name: "Cancel 3 runs?" })).toBeInTheDocument();
    expect(screen.getByText("This will cancel the current run and all queued runs for this workflow.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel all" }));

    await waitFor(() => expect(cancelCalls).toBe(1));
    expect(screen.queryByTitle("3 runs remaining")).not.toBeInTheDocument();
  });

  it("uses backend active and queued count before workflow cancellation", async () => {
    let cancelCalls = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      {
        job_id: "job-known",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      },
      (url, init) => {
        if (url.endsWith("/api/jobs/job-known/progress")) {
          return jsonResponse({
            job_id: "job-known",
            status: "queued",
            value: null,
            max: null,
            current_node: null,
            message: "Queued.",
          });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
          return jsonResponse({ active_count: 1, queued_count: 19, total_count: 20 });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") && init?.method === "POST") {
          cancelCalls += 1;
          return jsonResponse({
            canceled_active_count: 1,
            canceled_queued_count: 19,
            already_terminal_count: 0,
            failed_to_cancel_count: 0,
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByTitle("1 run remaining")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));

    expect(await screen.findByRole("dialog", { name: "Cancel 20 runs?" })).toBeInTheDocument();
    expect(cancelCalls).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Cancel all" }));

    await waitFor(() => expect(cancelCalls).toBe(1));
  });

  it("keeps completed outputs visible after workflow queue cancellation", async () => {
    let runIndex = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      () => {
        runIndex += 1;
        return {
          job_id: runIndex === 1 ? "job-complete" : `job-cancel-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url, init) => {
        if (url.endsWith("/api/jobs/job-complete/progress")) {
          return jsonResponse({
            job_id: "job-complete",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          });
        }
        if (url.endsWith("/api/jobs/job-complete/result")) {
          return jsonResponse({
            job_id: "job-complete",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url: "/api/jobs/job-complete/outputs/view?filename=done.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          });
        }
        const progressMatch = url.match(/\/api\/jobs\/(job-cancel-\d+)\/progress$/);
        if (progressMatch) {
          return jsonResponse({
            job_id: progressMatch[1],
            status: "queued",
            value: null,
            max: null,
            current_node: null,
            message: "Queued.",
          });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
          return jsonResponse({ active_count: 1, queued_count: 1, total_count: 2 });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") && init?.method === "POST") {
          return jsonResponse({
            canceled_active_count: 1,
            canceled_queued_count: 1,
            already_terminal_count: 0,
            failed_to_cancel_count: 0,
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    const output = await screen.findByRole("img", { name: /generated workflow output/i });
    expect(output).toHaveAttribute("src", "/api/jobs/job-complete/outputs/view?filename=done.png&subfolder=&type=output");

    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch count" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByTitle("2 runs remaining")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {
      name: "Cancel current run and all queued runs for this workflow",
    }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel all" }));

    await waitFor(() => expect(screen.queryByTitle("2 runs remaining")).not.toBeInTheDocument());
    expect(screen.getByRole("img", { name: /generated workflow output/i })).toHaveAttribute(
      "src",
      "/api/jobs/job-complete/outputs/view?filename=done.png&subfolder=&type=output",
    );
  });

  it("shows one compact summary for multiple batch failures instead of multiple dialogs", async () => {
    let runIndex = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      () => {
        runIndex += 1;
        return {
          job_id: `job-fail-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        const progressMatch = url.match(/\/api\/jobs\/(job-fail-\d+)\/progress$/);
        if (progressMatch) {
          const failed = progressMatch[1] === "job-fail-1" || progressMatch[1] === "job-fail-2";
          return jsonResponse({
            job_id: progressMatch[1],
            status: failed ? "failed" : "queued",
            value: failed ? 1 : null,
            max: failed ? 1 : null,
            current_node: null,
            message: failed ? `${progressMatch[1]} failed` : "Queued.",
          });
        }
        const resultMatch = url.match(/\/api\/jobs\/(job-fail-\d+)\/result$/);
        if (resultMatch) {
          return jsonResponse({
            job_id: resultMatch[1],
            status: "failed",
            outputs: [],
            error: `${resultMatch[1]} failed`,
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Batch count" }), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("2 runs failed")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Workflow failed" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("job-fail-1 failed")).toBeInTheDocument();
    expect(screen.getByText("job-fail-2 failed")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Logs" })).toHaveLength(2);
  });

  it("tracks submitted jobs with bounded polling instead of a job event stream", async () => {
    const eventSourceMock = vi.fn(function (this: { addEventListener: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> }) {
      this.addEventListener = vi.fn();
      this.close = vi.fn();
    });
    vi.stubGlobal("EventSource", eventSourceMock);
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
      expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/jobs/job-4/progress"))).toBe(true);
    });
    expect(eventSourceMock).not.toHaveBeenCalled();
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

  it("renders generated audio with backend-owned player, download, open, and Auto Save actions", async () => {
    const audioPackageData = {
      ...configuredPackageData,
      outputs: [{ id: "audio", label: "Audio", node_id: "12", type: "audio", kind: "audio" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-audio",
            type: "display_audio",
            label: "Audio result",
            output_id: "audio",
            layout: { x: 0, y: 0, w: 12, h: 6 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, audioPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-audio", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-audio/progress")) {
        return Promise.resolve(jsonResponse({ job_id: "job-audio", status: "completed", value: 1, max: 1, message: "Execution completed" }));
      }
      if (url.endsWith("/api/jobs/job-audio/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-audio",
          status: "completed",
          outputs: [{
            node_id: "12",
            output: {
              audio: [{
                filename: "speech.wav",
                kind: "audio",
                type: "audio",
                output_type: "output",
                mime_type: "audio/wav",
                size: 2048,
                duration_seconds: 2,
                url: "/api/jobs/job-audio/outputs/view?filename=speech.wav&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        }));
      }
      return configuredFetch(input, init);
    });
    let downloadUrl = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function captureDownload(this: HTMLAnchorElement) {
      downloadUrl = this.href;
    });
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    renderRunPage();

    expect(await screen.findByRole("button", { name: /enable auto save for audio result/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByText("speech.wav")).toBeInTheDocument();
    expect(document.querySelector("audio")).toHaveAttribute(
      "src",
      "/api/jobs/job-audio/outputs/view?filename=speech.wav&subfolder=&type=output",
    );
    expect(screen.getByText("WAV · 2 KB · 0:02")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(downloadUrl).toContain("download=true");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(openSpy).toHaveBeenCalledWith(
      "/api/jobs/job-audio/outputs/view?filename=speech.wav&subfolder=&type=output",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("renders generated video with backend-owned player, metadata, and Auto Save actions", async () => {
    const videoPackageData = {
      ...configuredPackageData,
      outputs: [{ id: "video", label: "Video", node_id: "15", type: "video", kind: "video" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-video",
            type: "display_video",
            label: "Video result",
            output_id: "video",
            layout: { x: 0, y: 0, w: 16, h: 14 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, videoPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-video", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-video/progress")) {
        return Promise.resolve(jsonResponse({ job_id: "job-video", status: "completed", value: 1, max: 1, message: "Execution completed" }));
      }
      if (url.endsWith("/api/jobs/job-video/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-video",
          status: "completed",
          outputs: [{
            node_id: "15",
            output: {
              images: [{
                filename: "clip.mp4",
                kind: "video",
                type: "video",
                output_type: "output",
                mime_type: "video/mp4",
                size: 4096,
                duration_seconds: 3,
                width: 1280,
                height: 720,
                fps: 24,
                url: "/api/jobs/job-video/outputs/view?filename=clip.mp4&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPage();

    expect(await screen.findByRole("button", { name: /enable auto save for video result/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByText("clip.mp4")).toBeInTheDocument();
    expect(document.querySelector("video")).toHaveAttribute(
      "src",
      "/api/jobs/job-video/outputs/view?filename=clip.mp4&subfolder=&type=output",
    );
    expect(screen.getByText("MP4 · 1280 × 720 · 24 fps · 4 KB · 0:03")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fullscreen" })).toBeInTheDocument();
  });

  it("renders dashboard-only notes as read-only canvas cards", async () => {
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, dashboardOnlyNotePackageData());

    renderRunPage();

    const noteBody = await screen.findByText("Use a square source image. Large images take longer.");
    expect(noteBody.closest(".dashboard-note-card")).toHaveClass("dashboard-note-card--canvas");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
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
    expect(screen.queryByText("The local ComfyUI engine is not reachable")).not.toBeInTheDocument();
  });

  it("explains why the canvas run button is disabled when required models are missing", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(missingModelSummary));
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
      if (url.endsWith("/api/models/downloads") && init?.method === "POST") {
        return Promise.resolve(jsonResponse({ job_id: "model-download-1", status: "queued", user_facing_message: "Downloading required models..." }));
      }
      if (url.endsWith("/api/models/downloads/model-download-1")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "model-download-1",
            status: "running",
            user_facing_message: "Downloading required models...",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            bytes_downloaded: 256,
            total_bytes: 1024,
            percent: 25,
            speed_bytes_per_second: 512,
            models: [
              {
                requirement_id: "checkpoint",
                filename: "v1-5-pruned-emaonly-fp16.safetensors",
                status: "running",
                status_label: "Downloading",
                message: "Downloading required model...",
                bytes_downloaded: 256,
                total_bytes: 1024,
                percent: 25,
              },
            ],
            model_summary: missingModelSummary,
          }),
        );
      }
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

    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    expect(runButton).toBeDisabled();
    expect(runButton).toHaveAttribute(
      "title",
      "Add required model before running: v1-5-pruned-emaonly-fp16.safetensors.",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Add required model before running: v1-5-pruned-emaonly-fp16.safetensors.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(screen.getByRole("dialog", { name: "Missing Models" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download Missing Models" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/models/downloads", {
        method: "POST",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        body: JSON.stringify({ selections: [{ workflow_id: "text_to_image_v0", requirement_id: "checkpoint" }] }),
      });
    });
    expect(await screen.findByRole("progressbar", { name: "Model download progress" })).toBeInTheDocument();
    expect(screen.queryByText("This workflow needs required models")).not.toBeInTheDocument();
  });

  it("shows the canvas Download action for required model blockers even before an automatic source is available", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
        return Promise.resolve(
          jsonResponse({
            ...missingModelSummary,
            needs_manual_download_count: 1,
            models: missingModelSummary.models.map((model) => ({
              ...model,
              status: "needs_manual_download",
              status_label: "Needs manual download",
              message: "Noofy does not have enough source information to download this model automatically.",
            })),
          }),
        );
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

    expect(await screen.findByRole("button", { name: "Download" })).toBeInTheDocument();
  });

  it("starts local model verification when the missing models popup sees a possible local match", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
        return Promise.resolve(
          jsonResponse({
            ...missingModelSummary,
            missing_count: 0,
            possible_match_count: 1,
            models: missingModelSummary.models.map((model) => ({
              ...model,
              status: "possible_match",
              status_label: "Possible match",
              source_path: "/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
              message: "A local file with this name was found, but Noofy needs stronger verification before using it.",
            })),
          }),
        );
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
      if (url.endsWith("/api/workflows/text_to_image_v0/model-verification") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "verification-1",
            workflow_id: "text_to_image_v0",
            status: "running",
            user_facing_message: "Verifying local model files...",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            verified_models: 0,
            percent: 0,
            models: [],
            model_summary: null,
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/model-verification/verification-1")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "verification-1",
            workflow_id: "text_to_image_v0",
            status: "running",
            user_facing_message: "Verifying local model files...",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            verified_models: 0,
            percent: 50,
            models: [],
            model_summary: null,
          }),
        );
      }
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

    fireEvent.click(await screen.findByRole("button", { name: "Download" }));

    expect(await screen.findByRole("progressbar", { name: "Model verification progress" })).toBeInTheDocument();
    expect(screen.getByText(/Verifying local model/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/text_to_image_v0/model-verification", expect.objectContaining({ method: "POST" }));
  });

  it("does not automatically retry local model verification after a failure", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
        return Promise.resolve(
          jsonResponse({
            ...missingModelSummary,
            missing_count: 0,
            possible_match_count: 1,
            models: missingModelSummary.models.map((model) => ({
              ...model,
              status: "possible_match",
              status_label: "Possible match",
            })),
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
        return Promise.resolve(jsonResponse({ workflow_id: "text_to_image_v0", valid: false, missing_models: [], errors: ["Missing model"] }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/model-verification") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            job_id: "verification-failed",
            workflow_id: "text_to_image_v0",
            status: "running",
            user_facing_message: "Verifying local model files...",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            verified_models: 0,
            percent: 0,
            models: [],
            model_summary: null,
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/model-verification/verification-failed")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "verification-failed",
            workflow_id: "text_to_image_v0",
            status: "failed",
            user_facing_message: "Model verification failed. Try again or use a different model file.",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            verified_models: 0,
            percent: 0,
            models: [],
            model_summary: null,
          }),
        );
      }
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

    fireEvent.click(await screen.findByRole("button", { name: "Download" }));

    expect(await screen.findByText("Model verification failed")).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    const starts = fetchMock.mock.calls.filter(([url, init]) =>
      String(url).endsWith("/api/workflows/text_to_image_v0/model-verification") &&
      (init as RequestInit | undefined)?.method === "POST"
    );
    expect(starts).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Verify Again" })).toBeInTheDocument();
  });

  it("uses the selected canvas shell while workflow data is loading", async () => {
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

    // Flush the fire-and-forget /api/resources fetch so its trailing setState lands inside act().
    await act(async () => {});
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

  it("keeps Run enabled while a silent runtime refresh is pending", async () => {
    const staleReadyRuntimeState = {
      ...readyRuntimeState,
      lastCheckedAt: Date.now() - 60_000,
    };
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return new Promise<Response>(() => {});
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

    renderRunPage({}, staleReadyRuntimeState);

    expect(await screen.findByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.queryByText("Checking Noofy")).not.toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
  });

  it("marks the backend offline after a run action fails", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.reject(new Error("backend down"));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
        return Promise.reject(new Error("run request failed"));
      }
      if (url.endsWith("/api/logs?limit=200")) {
        return Promise.resolve(
          jsonResponse({
            events: [
              diagnosticEvent("comfyui.stdout", "CUDA out of memory"),
              diagnosticEvent("runtime.manager", "Managed ComfyUI crashed", { pid: 123, returncode: 1 }),
              diagnosticEvent("engine.service", "Submitting workflow run", { runner_id: "runner-1" }),
              diagnosticEvent("workflow.models", "Model summary refreshed"),
            ],
          }),
        );
      }
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

    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("The workflow is not ready")).toBeInTheDocument();
    expect(screen.getAllByText("run request failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Service offline").length).toBeGreaterThan(0);
    expect(await screen.findByRole("dialog", { name: "Workflow failed" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "ComfyUI engine logs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Noofy logs" })).toBeInTheDocument();
    expect(screen.getByText(/CUDA out of memory/)).toBeInTheDocument();
    expect(screen.getByText(/Submitting workflow run/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /copy logs/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("ComfyUI engine logs");
    expect(copied).toContain("Noofy logs");
    expect(copied).toContain("CUDA out of memory");
    expect(copied).toContain("Submitting workflow run");
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

  it("renders dashboard-only notes as read-only cards in classic mode", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, dashboardOnlyNotePackageData());

    renderRunPage();

    expect(await screen.findByRole("heading", { name: "Before you run" })).toBeInTheDocument();
    expect(screen.getByText("Use a square source image. Large images take longer.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("menuitem", { name: /export as noofy/i }));
    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filename")).toHaveValue("Text to Image.noofy");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(optionsButton);
    fireEvent.click(screen.getByRole("menuitem", { name: /export comfyui json/i }));
    expect(screen.getByRole("dialog", { name: "Export workflow" })).toBeInTheDocument();
    expect(screen.getByLabelText("Filename")).toHaveValue("Text to Image.json");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(optionsButton);
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

  it("saves normal action bar drags only to user state", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    const dragHandle = await screen.findByRole("button", { name: /move workflow action bar/i });
    mockCanvasActionBarBounds({ left: 820, top: 12 });

    dispatchPointer(dragHandle, "pointerdown", { clientX: 830, clientY: 20 });
    dispatchPointer(window, "pointermove", { clientX: 780, clientY: 70 });
    dispatchPointer(window, "pointerup", { clientX: 780, clientY: 70 });

    await waitFor(() => {
      const userStatePut = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/user-state") &&
          (init as RequestInit | undefined)?.method === "PUT" &&
          Boolean(
            JSON.parse(((init as RequestInit | undefined)?.body as string | undefined) ?? "{}")
              .presentation_overrides?.action_bar,
          ),
      );
      expect(userStatePut).toBeDefined();
      const body = JSON.parse((userStatePut![1] as RequestInit).body as string);
      expect(body.presentation_overrides.action_bar).toEqual({
        x: 770,
        y: 62,
      });
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/dashboard") &&
          (init as RequestInit | undefined)?.method === "PUT",
      ),
    ).toBe(false);
  });

  it("keeps the desktop workflow action bar controls on one row", () => {
    expect(canvasCss).toMatch(/\.canvas-action-cluster\s*{[^}]*flex-wrap:\s*nowrap;/);
    expect(canvasCss).toMatch(/\.canvas-action-cluster__run\s*,\s*\.canvas-action-cluster__download\s*{[^}]*flex:\s*0 0 auto;/);
    expect(canvasCss).toMatch(/\.canvas-memory-loaded-pill\s*{[^}]*flex:\s*0 0 auto;/);
    expect(canvasCss).toMatch(/\.canvas-batch-count-stepper\s*{[^}]*flex:\s*0 0 auto;/);
    expect(canvasCss).toMatch(/\.canvas-action-cluster:not\(\.canvas-action-cluster--positioned\)\s*{[^}]*flex-wrap:\s*wrap;/);
  });

  it("uses a local action bar position before the creator-defined package position", async () => {
    const packageData = {
      ...configuredPackageData,
      dashboard: {
        ...configuredPackageData.dashboard,
        presentation: { action_bar: { x: 24, y: 32 } },
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
            presentation_overrides: { action_bar: { x: 140, y: 90 } },
          }),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await screen.findByRole("button", { name: /move workflow action bar/i });
    const actionBar = document.querySelector(".canvas-action-cluster") as HTMLElement;
    await waitFor(() => {
      expect(actionBar).toHaveStyle({ left: "140px", top: "90px" });
    });
  });

  it("restores native dashboard customizations from the canvas options menu", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /restore dashboard to the workflow default values/i }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/api/workflows/text_to_image_v0/dashboard") &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toBe(true);
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/user-state/layout") &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(true);
  });

  it("posts the current dashboard values when exporting ComfyUI JSON from the canvas", async () => {
    mockConfiguredDashboardFetch(fetchMock);
    const createObjectUrl = vi.fn(() => "blob:workflow-json");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    renderRunPage();

    const promptInput = await screen.findByRole("textbox");
    fireEvent.change(promptInput, { target: { value: "current visible prompt" } });
    fireEvent.click(screen.getByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /export comfyui json/i }));
    fireEvent.click(screen.getByRole("button", { name: "Export" }));

    await waitFor(() => {
      const exportCall = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith("/api/workflows/text_to_image_v0/export/comfyui-json") &&
        (init as RequestInit | undefined)?.method === "POST",
      );
      expect(exportCall).toBeTruthy();
      const body = JSON.parse(String((exportCall?.[1] as RequestInit).body));
      expect(body.input_values.prompt).toBe("current visible prompt");
    });
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

  it("saves action bar moves in layout edit mode to dashboard presentation", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    const dragHandle = screen.getByRole("button", { name: /move workflow action bar/i });
    mockCanvasActionBarBounds({ left: 24, top: 18 });
    dispatchPointer(dragHandle, "pointerdown", { clientX: 30, clientY: 22 });
    dispatchPointer(window, "pointermove", { clientX: 90, clientY: 82 });
    dispatchPointer(window, "pointerup", { clientX: 90, clientY: 82 });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const dashboardPut = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/dashboard") &&
          (init as RequestInit | undefined)?.method === "PUT",
      );
      expect(dashboardPut).toBeDefined();
      const body = JSON.parse((dashboardPut![1] as RequestInit).body as string);
      expect(body.dashboard.presentation.action_bar).toEqual({ x: 84, y: 78 });
    });
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

    fireEvent.change(await screen.findByRole("textbox"), { target: { value: "current visible prompt" } });
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
            defaultValue: "current visible prompt",
            layout: expect.objectContaining({ x: 0, y: 0, w: 16, h: 6 }),
          }),
        ]),
      }),
    );
  });

  it("opens image input widgets with the current uploaded asset value", async () => {
    const onEditWidgets = vi.fn();
    const assetId = "123e4567-e89b-12d3-a456-426614174000.png";
    const imagePackageData = {
      ...configuredPackageData,
      inputs: [
        {
          id: "source_image",
          label: "Input image",
          control: "load_image",
          binding: { node_id: "10", input_name: "image" },
          default: assetId,
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
              {
                id: "source_image",
                type: "load_image",
                label: "Input image",
                input_id: "source_image",
                layout: { x: 0, y: 0, w: 10, h: 8 },
              },
              {
                id: "result",
                type: "display_image",
                label: "Result",
                output_id: "image",
                layout: { x: 10, y: 0, w: 14, h: 10 },
              },
            ],
          },
        ],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, imagePackageData, null, (url) => {
      if (url.endsWith(`/api/assets/${assetId}/metadata`)) {
        return jsonResponse({
          asset_id: assetId,
          original_filename: "portrait.png",
          content_type: "image/png",
          kind: "image",
        });
      }
      if (url.endsWith(`/api/assets/${assetId}`)) {
        return new Response(new Blob(["input"], { type: "image/png" }), { status: 200 });
      }
      return undefined;
    });

    renderRunPage({ onEditWidgets });

    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit widgets/i }));

    expect(onEditWidgets).toHaveBeenCalledWith(
      expect.objectContaining({
        widgets: expect.arrayContaining([
          expect.objectContaining({
            id: "source_image",
            widgetType: "load_image",
            defaultValue: assetId,
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
    expect(screen.queryByRole("button", { name: /download result a image/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /download result b image/i })).not.toBeInTheDocument();

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
    expect(screen.queryByRole("slider", { name: /compare original image/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download result a image/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download result b image/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /open generated workflow output 2 full-screen/i }));

    expect(screen.getByRole("dialog", { name: /result a full-screen preview/i })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /generated workflow output 2 full-screen preview/i })).toHaveAttribute(
      "src",
      "/api/jobs/job-canvas/outputs/view?filename=node-9-b.png&subfolder=&type=output",
    );
    expect(screen.getByRole("textbox")).toHaveValue("a lake");

    const viewerImage = screen.getByRole("img", { name: /generated workflow output 2 full-screen preview/i });
    const viewerStage = viewerImage.parentElement as HTMLElement;
    vi.spyOn(viewerStage, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 800,
      width: 1000,
      height: 800,
      toJSON: () => ({}),
    } as DOMRect);
    Object.defineProperty(viewerImage, "naturalWidth", { configurable: true, value: 4096 });
    Object.defineProperty(viewerImage, "naturalHeight", { configurable: true, value: 2048 });

    fireEvent.load(viewerImage);

    await waitFor(() => {
      expect(viewerImage).toHaveStyle({ width: "1000px", height: "500px" });
    });

    fireEvent.doubleClick(viewerImage, { clientX: 750, clientY: 600 });

    await waitFor(() => {
      expect(viewerImage).toHaveStyle({ transform: "translate(-375px, -300px) scale(2.5)" });
    });

    const wheelEvent = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      clientX: 750,
      clientY: 600,
      ctrlKey: true,
      deltaY: -100,
    });

    expect(fireEvent(viewerStage, wheelEvent)).toBe(false);
    await waitFor(() => {
      expect(viewerImage.getAttribute("style")).toMatch(/scale\(4\.121803176750/);
    });
    const styleAfterWheel = viewerImage.getAttribute("style");

    fireEvent.pointerDown(viewerImage, { pointerId: 1, clientX: 400, clientY: 400 });
    fireEvent.pointerMove(viewerImage, { pointerId: 1, clientX: 460, clientY: 430 });
    fireEvent.pointerUp(viewerImage, { pointerId: 1, clientX: 460, clientY: 430 });

    await waitFor(() => {
      expect(viewerImage.getAttribute("style")).not.toBe(styleAfterWheel);
      expect(viewerImage.getAttribute("style")).toMatch(/scale\(4\.121803176750/);
    });

    fireEvent.click(screen.getByRole("button", { name: /reset view/i }));

    await waitFor(() => {
      expect(viewerImage).toHaveStyle({ transform: "translate(0px, 0px) scale(1)" });
    });

    fireEvent.click(screen.getByRole("button", { name: /close full-screen image preview/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: /result a full-screen preview/i })).not.toBeInTheDocument();
    });
    expect(screen.getByText("Result A")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("a lake");

    fireEvent.click(screen.getByRole("button", { name: /download result a image/i }));

    await waitFor(() => expect(createObjectUrl).toHaveBeenCalled());
    expect(clickedDownload).toBe("node-9-a.png");
    await waitFor(() => expect(revokeObjectUrl).toHaveBeenCalledWith("blob:noofy-output"));
  });

  it("shows a before and after comparison in display_image when the run used an input image", async () => {
    const assetId = "123e4567-e89b-12d3-a456-426614174000.png";
    let objectUrlIndex = 0;
    const createObjectUrl = vi.fn(() => {
      objectUrlIndex += 1;
      return `blob:input-image-${objectUrlIndex}`;
    });
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });

    const packageData = {
      metadata: {
        id: "text_to_image_v0",
        name: "Image Edit",
        version: "0.1.0",
        description: "",
      },
      inputs: [
        {
          id: "source_image",
          label: "Input image",
          control: "load_image",
          binding: { node_id: "10", input_name: "image" },
          default: assetId,
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
              {
                id: "source_image",
                type: "load_image",
                label: "Input image",
                input_id: "source_image",
                layout: { x: 0, y: 0, w: 10, h: 8 },
              },
              {
                id: "result",
                type: "display_image",
                label: "Result",
                output_id: "image",
                layout: { x: 10, y: 0, w: 14, h: 10 },
              },
            ],
          },
        ],
      },
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(packageData));
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith(`/api/assets/${assetId}/metadata`)) {
        return Promise.resolve(
          jsonResponse({
            asset_id: assetId,
            original_filename: "portrait.png",
            content_type: "image/png",
          }),
        );
      }
      if (url.endsWith(`/api/assets/${assetId}`)) {
        return Promise.resolve(new Response(new Blob(["input"], { type: "image/png" }), { status: 200 }));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
        return Promise.resolve(
          jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: "0.1.0",
            values: { source_image: assetId },
            layout_overrides: {},
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        expect(init?.method).toBe("POST");
        const body = JSON.parse(String(init?.body));
        expect(body.inputs.source_image).toBe(assetId);
        return Promise.resolve(
          jsonResponse({
            job_id: "job-image-edit",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-image-edit/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-image-edit",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-image-edit/result")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-image-edit",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-image-edit/outputs/view?filename=edited.png&subfolder=&type=output",
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

    expect(await screen.findByAltText("Selected image: portrait.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const slider = await screen.findByRole("slider", { name: /compare original image/i });
    expect(slider).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-image-edit/outputs/view?filename=edited.png&subfolder=&type=output",
    );

    fireEvent.keyDown(slider, { key: "ArrowRight" });
    await waitFor(() => expect(slider).toHaveAttribute("aria-valuenow", "5"));

    fireEvent.click(screen.getByRole("button", { name: /open generated workflow output full-screen/i }));

    expect(screen.getByRole("dialog", { name: /result full-screen preview/i })).toBeInTheDocument();
    const fullScreenSlider = screen.getAllByRole("slider", { name: /compare original image/i })[1];
    const viewerComparison = fullScreenSlider.closest(".widget-image-viewer__comparison") as HTMLElement;
    const viewerStage = viewerComparison.parentElement as HTMLElement;
    const fullScreenImage = screen.getByAltText("Generated workflow output full-screen preview");
    vi.spyOn(viewerStage, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 800,
      width: 1000,
      height: 800,
      toJSON: () => ({}),
    } as DOMRect);
    vi.spyOn(fullScreenSlider.parentElement as HTMLElement, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 500,
      width: 1000,
      height: 500,
      toJSON: () => ({}),
    } as DOMRect);
    Object.defineProperty(fullScreenImage, "naturalWidth", { configurable: true, value: 4096 });
    Object.defineProperty(fullScreenImage, "naturalHeight", { configurable: true, value: 2048 });

    fireEvent.load(fullScreenImage);

    await waitFor(() => {
      expect(viewerComparison).toHaveStyle({ width: "1000px", height: "500px" });
    });

    expect(fullScreenSlider).toHaveAttribute("aria-valuenow", "0");

    fireEvent.pointerDown(fullScreenSlider, { pointerId: 1, clientX: 500 });
    fireEvent.pointerUp(fullScreenSlider, { pointerId: 1, clientX: 500 });
    await waitFor(() => expect(fullScreenSlider).toHaveAttribute("aria-valuenow", "50"));
    expect(slider).toHaveAttribute("aria-valuenow", "5");

    fireEvent.doubleClick(viewerComparison, { clientX: 750, clientY: 600 });
    await waitFor(() => {
      expect(viewerComparison).toHaveStyle({ transform: "translate(-375px, -300px) scale(2.5)" });
    });

    fireEvent.click(screen.getByRole("button", { name: /reset view/i }));
    await waitFor(() => {
      expect(viewerComparison).toHaveStyle({ transform: "translate(0px, 0px) scale(1)" });
    });
  });
});
