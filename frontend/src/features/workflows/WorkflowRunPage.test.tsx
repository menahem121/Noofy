import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RuntimeStatusProvider, type RuntimeHealthState } from "../app/RuntimeStatusProvider";
import { splitDiagnosticLogs, WorkflowRunPage } from "./WorkflowRunPage";

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
  runtimeResponse = readyRuntime,
) {
  fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
    if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(runtimeResponse));
    if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
    if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
    if (url.endsWith("/api/workflows/text_to_image_v0/package")) return Promise.resolve(jsonResponse(configuredPackageData));
    if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) return Promise.resolve(jsonResponse(readyModelSummary));
    if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
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
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

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
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const topBarProgress = await screen.findByRole("progressbar", { name: /workflow progress/i });
    expect(topBarProgress).toHaveAttribute("aria-valuenow", "0");
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByText("Starting workflow...")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
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
});
