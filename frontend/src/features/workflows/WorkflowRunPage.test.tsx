import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { StrictMode, useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResourceStatusProvider } from "../app/ResourceStatusProvider";
import { RuntimeStatusProvider, type RuntimeHealthState } from "../app/RuntimeStatusProvider";
import { WorkflowTabsProvider, WorkflowTabsRouteProvider, useWorkflowTabs, type WorkflowTabRuntimeState } from "../app/WorkflowTabs";
import { splitDiagnosticLogs, WorkflowRunPage } from "./WorkflowRunPage";
import { __resetWorkflowUserStateCacheForTests } from "../../lib/useWorkflowUserState";
import { invalidateWorkflowRunPageCache, resetWorkflowRunPageCacheForTests } from "./workflowRunPageCache";

vi.mock("../three-d/threeDScene", () => ({
  createThreeDScene: vi.fn().mockResolvedValue({
    animations: [],
    dispose: vi.fn(),
  }),
}));

const canvasCss = readFileSync(resolve(process.cwd(), "src/styles/canvas.css"), "utf8");
const componentsCss = readFileSync(resolve(process.cwd(), "src/styles/components.css"), "utf8");

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

function binaryResponse(data: string, contentType: string, status = 200) {
  return new Response(new TextEncoder().encode(data), {
    status,
    headers: {
      "Content-Type": contentType,
    },
  });
}

function imageResponse(data = "image", status = 200) {
  return binaryResponse(data, "image/png", status);
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

const engineBusyRuntimeState: Partial<RuntimeHealthState> = {
  ...readyRuntimeState,
  engineStatus: "busy",
  runtime: {
    ...readyRuntime,
    reachable: true,
    transient_health_failure: true,
    last_reachable_at: "2026-06-09T10:00:00+00:00",
    error: "health_check_timeout",
  } as RuntimeHealthState["runtime"],
};

const backendOfflineRuntimeState: Partial<RuntimeHealthState> = {
  ...engineOfflineRuntimeState,
  backendStatus: "unreachable",
  refreshError: "The local service did not answer in time.",
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

const downloadedModelSummary = {
  ...missingModelSummary,
  available_count: 1,
  missing_count: 0,
  ready_to_run: true,
  models: missingModelSummary.models.map((model) => ({
    ...model,
    status: "available",
    status_label: "Available",
    source_path: "/models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors",
    matched_root: "/models",
    message: null,
  })),
};

const workflowStatus = {
  workflow_id: "text_to_image_v0",
  workflow: {
    id: "text_to_image_v0",
    name: "Text to Image",
    version: "0.1.0",
    description: "Generate new images from a simple text prompt.",
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

const imageInputPackageData = {
  ...configuredPackageData,
  inputs: [
    {
      id: "source_image",
      label: "Source image",
      control: "load_image",
      binding: { node_id: "267:276", input_name: "image" },
      default: null,
      validation: { required: true },
    },
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
            id: "ctrl-source-image",
            type: "load_image",
            label: "Source image",
            input_id: "source_image",
            required: true,
            layout: { x: 0, y: 0, w: 12, h: 6 },
          },
        ],
      },
    ],
  },
};

const allEditableWidgetTypesPackageData = {
  ...configuredPackageData,
  inputs: [
    {
      id: "title",
      label: "Title",
      control: "string_field",
      binding: { node_id: "1", input_name: "title" },
      default: "package title",
      validation: {},
    },
    {
      id: "prompt",
      label: "Prompt",
      control: "textarea",
      binding: { node_id: "2", input_name: "text" },
      default: "package prompt",
      validation: {},
    },
    {
      id: "steps",
      label: "Steps",
      control: "int_field",
      binding: { node_id: "3", input_name: "steps" },
      default: 24,
      validation: { min: 1, max: 80, step: 1 },
    },
    {
      id: "height",
      label: "Height",
      control: "slider",
      binding: { node_id: "4", input_name: "height" },
      default: 768,
      validation: { min: 64, max: 2048, step: 64 },
    },
    {
      id: "random_seed",
      label: "Seed",
      control: "seed_widget",
      binding: { node_id: "5", input_name: "seed" },
      default: 1234,
      validation: {},
    },
    {
      id: "enabled",
      label: "Enabled",
      control: "toggle",
      binding: { node_id: "6", input_name: "enabled" },
      default: false,
      validation: {},
    },
    {
      id: "sampler",
      label: "Sampler",
      control: "select",
      binding: { node_id: "7", input_name: "sampler_name" },
      default: "euler",
      validation: { options: ["euler", "dpmpp_2m"] },
    },
    {
      id: "style_lora",
      label: "Style LoRA",
      control: "lora_loader",
      binding: { node_id: "8", input_name: "lora_name" },
      default: "",
      validation: { options: ["None", "package-style.safetensors", "runtime-style.safetensors"] },
    },
    {
      id: "source_image",
      label: "Source image",
      control: "load_image",
      binding: { node_id: "9", input_name: "image" },
      default: "package-image.png",
      validation: { accepted_extensions: [".png"] },
    },
    {
      id: "mask_image",
      label: "Mask image",
      control: "load_image_mask",
      binding: { node_id: "10", input_name: "mask" },
      default: "package-mask.png",
      validation: { accepted_extensions: [".png"] },
    },
    {
      id: "source_audio",
      label: "Source audio",
      control: "load_audio",
      binding: { node_id: "11", input_name: "audio" },
      default: "package-audio.wav",
      validation: { accepted_extensions: [".wav"] },
    },
    {
      id: "source_video",
      label: "Source video",
      control: "load_video",
      binding: { node_id: "12", input_name: "video" },
      default: "package-video.mp4",
      validation: { accepted_extensions: [".mp4"] },
    },
    {
      id: "source_file",
      label: "Source file",
      control: "load_file",
      binding: { node_id: "13", input_name: "file" },
      default: "package-file.json",
      validation: { accepted_extensions: [".json"] },
    },
    {
      id: "source_3d",
      label: "Source 3D",
      control: "load_3d",
      binding: { node_id: "14", input_name: "model" },
      default: "package-model.glb",
      validation: { accepted_extensions: [".glb"] },
    },
    {
      id: "note_value",
      label: "Note value",
      control: "string_field",
      binding: { node_id: "15", input_name: "note" },
      default: "package note",
      validation: {},
    },
    {
      id: "hidden_strength",
      label: "Hidden strength",
      control: "slider",
      binding: { node_id: "16", input_name: "strength" },
      default: 0.35,
      default_pinned: true,
      validation: { min: 0, max: 1, step: 0.05 },
    },
  ],
  outputs: [
    { id: "image", label: "Image", node_id: "20", type: "image" },
    { id: "audio", label: "Audio", node_id: "21", type: "audio", kind: "audio" },
    { id: "video", label: "Video", node_id: "22", type: "video", kind: "video" },
    { id: "file", label: "File", node_id: "23", type: "file", kind: "file" },
    { id: "model", label: "Model", node_id: "24", type: "3d", kind: "3d" },
  ],
  dashboard: {
    version: "0.1.0",
    status: "configured",
    sections: [
      {
        id: "main",
        title: "Main",
        controls: [
          { id: "title", type: "string_field", label: "Title", input_id: "title", layout: { x: 0, y: 0, w: 8, h: 4 } },
          { id: "prompt", type: "textarea", label: "Prompt", input_id: "prompt", layout: { x: 8, y: 0, w: 10, h: 6 } },
          { id: "steps", type: "int_field", label: "Steps", input_id: "steps", layout: { x: 18, y: 0, w: 6, h: 4 } },
          { id: "height", type: "slider", label: "Height", input_id: "height", layout: { x: 24, y: 0, w: 8, h: 4 } },
          { id: "random_seed", type: "seed_widget", label: "Seed", input_id: "random_seed", layout: { x: 0, y: 6, w: 8, h: 4 } },
          { id: "enabled", type: "toggle", label: "Enabled", input_id: "enabled", layout: { x: 8, y: 6, w: 6, h: 4 } },
          { id: "sampler", type: "select", label: "Sampler", input_id: "sampler", layout: { x: 14, y: 6, w: 8, h: 4 } },
          { id: "style_lora", type: "lora_loader", label: "Style LoRA", input_id: "style_lora", layout: { x: 22, y: 6, w: 10, h: 4 } },
          { id: "source_image", type: "load_image", label: "Source image", input_id: "source_image", layout: { x: 0, y: 10, w: 8, h: 6 } },
          { id: "mask_image", type: "load_image_mask", label: "Mask image", input_id: "mask_image", layout: { x: 8, y: 10, w: 8, h: 6 } },
          { id: "source_audio", type: "load_audio", label: "Source audio", input_id: "source_audio", layout: { x: 16, y: 10, w: 8, h: 6 } },
          { id: "source_video", type: "load_video", label: "Source video", input_id: "source_video", layout: { x: 24, y: 10, w: 8, h: 6 } },
          { id: "source_file", type: "load_file", label: "Source file", input_id: "source_file", layout: { x: 0, y: 16, w: 8, h: 6 } },
          { id: "source_3d", type: "load_3d", label: "Source 3D", input_id: "source_3d", layout: { x: 8, y: 16, w: 8, h: 6 } },
          { id: "note-card", type: "note", label: "Note", description: "Read this first.", input_id: "note_value", layout: { x: 16, y: 16, w: 8, h: 4 } },
          { id: "result-image", type: "display_image", label: "Result image", output_id: "image", layout: { x: 24, y: 16, w: 8, h: 6 } },
          { id: "result-audio", type: "display_audio", label: "Result audio", output_id: "audio", layout: { x: 0, y: 22, w: 8, h: 6 } },
          { id: "result-video", type: "display_video", label: "Result video", output_id: "video", layout: { x: 8, y: 22, w: 8, h: 6 } },
          { id: "result-file", type: "display_file", label: "Result file", output_id: "file", layout: { x: 16, y: 22, w: 8, h: 6 } },
          { id: "result-3d", type: "display_3d", label: "Result 3D", output_id: "model", layout: { x: 24, y: 22, w: 8, h: 6 } },
        ],
      },
    ],
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

interface WorkflowPackageUserStateVersionFixture {
  inputs: Array<{
    id: string;
    control: string;
    binding: unknown;
    default: unknown;
    validation: unknown;
  }>;
  dashboard: {
    version: string;
    sections: Array<{
      controls: Array<{
        id: string;
        type: string;
        input_id?: string;
        output_id?: string;
      }>;
      groups?: Array<{
        id: string;
        control_ids: string[];
        layout?: unknown;
      }>;
    }>;
  };
}

function dashboardUserStateVersionForTest(packageData: WorkflowPackageUserStateVersionFixture): string {
  const valueStateShape = {
    inputs: packageData.inputs.map((input) => ({
      id: input.id,
      control: input.control,
      binding: input.binding,
      default: input.default,
      validation: input.validation,
    })),
    controls: packageData.dashboard.sections.flatMap((section) =>
      section.controls.map((control) => ({
        id: control.id,
        type: control.type,
        input_id: control.input_id,
        output_id: control.output_id,
      })),
    ),
    groups: packageData.dashboard.sections.flatMap((section) =>
      (section.groups ?? []).map((group) => ({
        id: group.id,
        control_ids: group.control_ids,
        layout: group.layout,
      })),
    ),
  };

  return `${packageData.dashboard.version}:${hashStringForTest(stableJsonForTest(valueStateShape))}`;
}

function stableJsonForTest(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => stableJsonForTest(item)).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJsonForTest(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function hashStringForTest(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

function renderRunPage(
  props: Partial<ComponentProps<typeof WorkflowRunPage>> = {},
  runtimeState: Partial<RuntimeHealthState> = readyRuntimeState,
) {
  return render(
    <RuntimeStatusProvider initialRuntimeState={runtimeState} skipInitialRefresh>
      <ResourceStatusProvider initialSnapshot={resourceSnapshot} skipInitialRefresh>
        <WorkflowRunPage
          workflowId="text_to_image_v0"
          onBack={vi.fn()}
          onNavigate={vi.fn()}
          {...props}
        />
      </ResourceStatusProvider>
    </RuntimeStatusProvider>,
  );
}

function renderRunPageWithWorkflowRuntime(
  workflowRuntime: Partial<WorkflowTabRuntimeState>,
  props: Partial<ComponentProps<typeof WorkflowRunPage>> = {},
  runtimeState: Partial<RuntimeHealthState> = readyRuntimeState,
) {
  return render(
    <RuntimeStatusProvider initialRuntimeState={runtimeState} skipInitialRefresh>
      <ResourceStatusProvider initialSnapshot={resourceSnapshot} skipInitialRefresh>
        <WorkflowTabsProvider>
          <WorkflowRuntimeSeeder workflowRuntime={workflowRuntime}>
            <WorkflowRunPage
              workflowId="text_to_image_v0"
              onBack={vi.fn()}
              onNavigate={vi.fn()}
              {...props}
            />
          </WorkflowRuntimeSeeder>
        </WorkflowTabsProvider>
      </ResourceStatusProvider>
    </RuntimeStatusProvider>,
  );
}

function WorkflowRuntimeSeeder({
  children,
  workflowRuntime,
}: {
  children: ReactNode;
  workflowRuntime: Partial<WorkflowTabRuntimeState>;
}) {
  const { setWorkflowRuntime } = useWorkflowTabs();
  useEffect(() => {
    setWorkflowRuntime("text_to_image_v0", workflowRuntime);
  }, [workflowRuntime, setWorkflowRuntime]);
  return <>{children}</>;
}

function WorkflowTabSwitchRunHarness() {
  const [activeWorkflowId, setActiveWorkflowId] = useState("first-workflow");
  return (
    <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
      <ResourceStatusProvider initialSnapshot={resourceSnapshot} skipInitialRefresh>
        <WorkflowTabsProvider>
          <WorkflowTabsRouteProvider
            activeWorkflowId={activeWorkflowId}
            onActivateWorkflowTab={(workflowId) => setActiveWorkflowId(workflowId)}
            onRequestCloseWorkflowTab={vi.fn()}
          >
            <WorkflowTabSwitchSeeder />
            <WorkflowRunPage workflowId={activeWorkflowId} onBack={vi.fn()} onNavigate={vi.fn()} />
          </WorkflowTabsRouteProvider>
        </WorkflowTabsProvider>
      </ResourceStatusProvider>
    </RuntimeStatusProvider>
  );
}

function WorkflowTabSwitchSeeder() {
  const { openWorkflowTab } = useWorkflowTabs();
  useEffect(() => {
    openWorkflowTab("first-workflow", "First Workflow");
    openWorkflowTab("next-workflow", "Next Workflow");
  }, [openWorkflowTab]);
  return null;
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
    window.sessionStorage.clear();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
  });

  afterEach(() => {
    resetWorkflowRunPageCacheForTests();
    __resetWorkflowUserStateCacheForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    window.localStorage.clear();
    window.sessionStorage.clear();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("acquires the runner lease late when the isolated runner binds after the page opened", async () => {
    let leaseOpenCount = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url, init) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/runner/leases") && init?.method === "POST") {
        leaseOpenCount += 1;
        if (leaseOpenCount === 1) {
          // No isolated runner is bound yet when the page first opens.
          return jsonResponse({ workflow_id: "text_to_image_v0", status: "no_runner", lease_id: null, runner: null });
        }
        return jsonResponse({
          workflow_id: "text_to_image_v0",
          status: "idle_warm",
          lease_id: "lease-late-1",
          runner: { runner_id: "isolated-1", open_workflow_lease_count: 1 },
        });
      }
      if (url.endsWith("/api/jobs/job-late/progress")) {
        return jsonResponse({ job_id: "job-late", status: "running", value: 1, max: 10, current_node: null, message: null });
      }
      return undefined;
    });

    function Harness({ runtime }: { runtime: Partial<WorkflowTabRuntimeState> }) {
      return (
        <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
          <WorkflowTabsProvider>
            <WorkflowRuntimeSeeder workflowRuntime={runtime}>
              <WorkflowRunPage workflowId="text_to_image_v0" onBack={vi.fn()} onNavigate={vi.fn()} />
            </WorkflowRuntimeSeeder>
          </WorkflowTabsProvider>
        </RuntimeStatusProvider>
      );
    }

    const view = render(<Harness runtime={{}} />);
    await waitForReadyStatus();
    await waitFor(() => expect(leaseOpenCount).toBe(1));

    // The first run starts and binds the isolated runner after the page was
    // already open: the tab now tracks a run handle, so the page re-attempts
    // and acquires the lease that protects the runner from closed-view release.
    view.rerender(
      <Harness
        runtime={{
          activeJobId: "job-late",
          activeJobStatus: "running",
          handleSource: "job",
          queueId: null,
        }}
      />,
    );

    await waitFor(() => expect(leaseOpenCount).toBe(2));
    // Once a lease is held no further attempts are made.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(leaseOpenCount).toBe(2);
  });

  it("does not repeat no-runner lease probes while the same unbound run is tracked", async () => {
    let leaseOpenCount = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url, init) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/runner/leases") && init?.method === "POST") {
        leaseOpenCount += 1;
        return jsonResponse({ workflow_id: "text_to_image_v0", status: "no_runner", lease_id: null, runner: null });
      }
      if (url.endsWith("/api/jobs/job-unbound/progress")) {
        return jsonResponse({ job_id: "job-unbound", status: "running", value: 1, max: 10, current_node: null, message: null });
      }
      return undefined;
    });

    function Harness({ runtime }: { runtime: Partial<WorkflowTabRuntimeState> }) {
      return (
        <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
          <WorkflowTabsProvider>
            <WorkflowRuntimeSeeder workflowRuntime={runtime}>
              <WorkflowRunPage workflowId="text_to_image_v0" onBack={vi.fn()} onNavigate={vi.fn()} />
            </WorkflowRuntimeSeeder>
          </WorkflowTabsProvider>
        </RuntimeStatusProvider>
      );
    }

    const runningRuntime: Partial<WorkflowTabRuntimeState> = {
      activeJobId: "job-unbound",
      activeJobStatus: "running",
      handleSource: "job",
      queueId: null,
    };
    const view = render(<Harness runtime={{}} />);
    await waitForReadyStatus();
    await waitFor(() => expect(leaseOpenCount).toBe(1));

    view.rerender(<Harness runtime={runningRuntime} />);
    await waitFor(() => expect(leaseOpenCount).toBe(2));

    view.rerender(<Harness runtime={{ ...runningRuntime }} />);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(leaseOpenCount).toBe(2);
  });

  it("splits diagnostics into ComfyUI engine logs and Noofy logs from existing sources", () => {
    const { comfyuiLogs, noofyLogs } = splitDiagnosticLogs([
      diagnosticEvent("comfyui.adapter", "ComfyUI execution failed"),
      diagnosticEvent("comfyui.stdout", "model load failed"),
      diagnosticEvent("runtime.manager", "Managed ComfyUI crashed"),
      diagnosticEvent("runtime.runner_process.stdout", "runner traceback"),
      diagnosticEvent("future.engine", "runner failed", { runner_id: "runner-1" }),
      diagnosticEvent("engine.service", "Submitting workflow run", { runner_id: "runner-1" }),
      diagnosticEvent("runs.orchestrator", "Workflow run blocked", { runner_id: "runner-1" }),
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
      "runs.orchestrator",
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

  it("asks the user to refresh instead of showing an empty workflow when the package cannot load", async () => {
    const reloadPage = vi.fn();
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) {
        return jsonResponse({ detail: "Workflow package is unavailable." }, 503);
      }
      return undefined;
    });

    render(
      <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh reloadPage={reloadPage}>
        <WorkflowRunPage
          workflowId="text_to_image_v0"
          onBack={vi.fn()}
          onNavigate={vi.fn()}
        />
      </RuntimeStatusProvider>,
    );

    const dialog = await screen.findByRole("dialog", { name: "Reload this workflow" });
    expect(within(dialog).getByText("Noofy could not load this workflow. Reload it before continuing.")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Reload workflow" }));

    expect(reloadPage).toHaveBeenCalledTimes(1);
  });

  it("reports a missing workflow instead of asking for an impossible reload", async () => {
    const onMissingWorkflow = vi.fn();
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) {
        return jsonResponse({ detail: "Workflow not found." }, 404);
      }
      return undefined;
    });

    renderRunPage({ onMissingWorkflow });

    expect(await screen.findByRole("heading", { name: "Workflow not installed" })).toBeInTheDocument();
    await waitFor(() => {
      expect(onMissingWorkflow).toHaveBeenCalledWith("text_to_image_v0");
    });
    expect(screen.queryByRole("dialog", { name: "Reload this workflow" })).not.toBeInTheDocument();
  });

  it("does not show the refresh fallback when a fresh page adopts the current backend session", async () => {
    const reloadPage = vi.fn();
    window.sessionStorage.setItem("noofy.tabBackendSession.v1", "bs-previous");
    mockConfiguredDashboardFetch(fetchMock, { ...readyRuntime, backend_session_id: "bs-current" });

    render(
      <RuntimeStatusProvider
        initialRuntimeState={{ ...readyRuntimeState, lastCheckedAt: Date.now() - 60_000 }}
        skipInitialRefresh
        reloadPage={reloadPage}
      >
        <WorkflowRunPage
          workflowId="text_to_image_v0"
          onBack={vi.fn()}
          onNavigate={vi.fn()}
        />
      </RuntimeStatusProvider>,
    );

    expect(await screen.findByRole("main", { name: /workflow dashboard canvas/i })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Reload this workflow" })).not.toBeInTheDocument();
    expect(reloadPage).not.toHaveBeenCalled();
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
    const refreshedPackage = deferred<Response>();
    let packageFetchCount = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/package")) {
        packageFetchCount += 1;
        return packageFetchCount === 1
          ? Promise.resolve(jsonResponse(loraPackageData))
          : refreshedPackage.promise;
      }
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
    await waitFor(() => expect(packageFetchCount).toBe(2));
    expect(screen.queryByText("Loading saved inputs")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run workflow" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.filter(([url, init]) =>
        String(url).endsWith("/api/workflows/text_to_image_v0/user-state")
        && (!init?.method || init.method === "GET"),
      ),
    ).toHaveLength(1);

    await act(async () => {
      refreshedPackage.resolve(jsonResponse(loraPackageData));
    });
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

  it("keeps the completed result across run-page remounts until its workflow cache is closed", async () => {
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

    const initialView = renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output",
    );

    initialView.unmount();
    const restoredView = renderRunPage();

    expect(await screen.findByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output",
    );

    restoredView.unmount();
    invalidateWorkflowRunPageCache("text_to_image_v0");
    renderRunPage();

    await waitForReadyStatus();
    expect(screen.queryByAltText("Generated workflow output")).not.toBeInTheDocument();
  });

  it("keeps live preview bytes locally until final media replaces them", async () => {
    const resultRequest = deferred<Response>();
    const livePreviewDataUrl = "data:image/png;base64,bGl2ZS1wcmV2aWV3";
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(
          jsonResponse({
            job_id: "job-live",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-live/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-live",
            status: "running",
            value: 1,
            max: 2,
            current_node: "7",
            message: "Sampling",
            live_preview_sequence: 1,
            live_preview: {
              sequence: 1,
              kind: "image",
              mime_type: "image/png",
              data_url: livePreviewDataUrl,
              node_id: "7",
              prompt_id: "job-live",
              target_node_ids: ["9"],
            },
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-live/progress?since_preview_sequence=1")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-live",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
            live_preview_sequence: 1,
            live_preview: {
              sequence: 1,
              kind: "image",
              mime_type: "image/png",
              data_url: null,
              node_id: "7",
              prompt_id: "job-live",
              target_node_ids: ["9"],
            },
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-live/result")) {
        return resultRequest.promise;
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByAltText("Live generation preview")).toHaveAttribute("src", livePreviewDataUrl);

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/api/jobs/job-live/progress?since_preview_sequence=1"),
        ),
      ).toBe(true);
    }, { timeout: 2500 });
    expect(screen.getByAltText("Live generation preview")).toHaveAttribute("src", livePreviewDataUrl);

    resultRequest.resolve(
      jsonResponse({
        job_id: "job-live",
        status: "completed",
        outputs: [
          {
            node_id: "9",
            output: {
              images: [
                {
                  view_url:
                    "/api/jobs/job-live/outputs/view?filename=result.png&subfolder=&type=output",
                },
              ],
            },
          },
        ],
        error: null,
      }),
    );

    await waitFor(() => {
      expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
        "src",
        "/api/jobs/job-live/outputs/view?filename=result.png&subfolder=&type=output",
      );
    });
    expect(screen.queryByAltText("Live generation preview")).not.toBeInTheDocument();
  });

  it.each(["classic", "canvas"] as const)(
    "shows a new live preview over the previous result and retains each image until its replacement loads in %s mode",
    async (viewMode) => {
      window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode }));
      const terminalProgressRequest = deferred<Response>();
      const secondResultRequest = deferred<Response>();
      const livePreviewDataUrl = "data:image/png;base64,bmV3LWxpdmUtcHJldmlldw==";
      let submittedRunCount = 0;

      mockConfiguredDashboardFetch(
        fetchMock,
        readyRuntime,
        configuredPackageData,
        (init?: RequestInit) => {
          expect(init?.method).toBe("POST");
          submittedRunCount += 1;
          const jobId = submittedRunCount === 1 ? "job-previous" : "job-current";
          return {
            job_id: jobId,
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          };
        },
        (url) => {
          if (url.endsWith("/api/jobs/job-previous/progress")) {
            return jsonResponse({
              job_id: "job-previous",
              status: "completed",
              value: 1,
              max: 1,
              current_node: null,
              message: "Execution completed",
            });
          }
          if (url.endsWith("/api/jobs/job-previous/result")) {
            return jsonResponse({
              job_id: "job-previous",
              status: "completed",
              outputs: [
                {
                  node_id: "9",
                  output: {
                    images: [
                      {
                        view_url:
                          "/api/jobs/job-previous/outputs/view?filename=previous.png&subfolder=&type=output",
                      },
                    ],
                  },
                },
              ],
              error: null,
            });
          }
          if (url.endsWith("/api/jobs/job-current/progress")) {
            return jsonResponse({
              job_id: "job-current",
              status: "running",
              value: 1,
              max: 2,
              current_node: "7",
              message: "Sampling",
              live_preview_sequence: 1,
              live_preview: {
                sequence: 1,
                kind: "image",
                mime_type: "image/png",
                data_url: livePreviewDataUrl,
                node_id: "7",
                prompt_id: "job-current",
                target_node_ids: ["9"],
              },
            });
          }
          if (url.endsWith("/api/jobs/job-current/progress?since_preview_sequence=1")) {
            return terminalProgressRequest.promise;
          }
          if (url.endsWith("/api/jobs/job-current/result")) {
            return secondResultRequest.promise;
          }
          return undefined;
        },
      );

      renderRunPage();
      act(() => {
        window.dispatchEvent(new Event("noofy:prefs-changed"));
      });

      await waitForReadyStatus();
      const runButton = screen.getByRole("button", { name: /run workflow/i });
      await waitFor(() => expect(runButton).toBeEnabled());
      fireEvent.click(runButton);

      const previousImage = await screen.findByAltText("Generated workflow output");
      fireEvent.load(previousImage);
      await waitFor(() => {
        expect(screen.getByAltText("Generated workflow output")).not.toHaveClass("retained-image--pending");
      });

      await waitFor(() => expect(runButton).toBeEnabled());
      fireEvent.click(runButton);

      const pendingLivePreview = await screen.findByAltText("Live generation preview");
      expect(pendingLivePreview).toHaveClass("retained-image--pending");
      expect(document.querySelector('img[src*="filename=previous.png"]')).toHaveAttribute("aria-hidden", "true");

      fireEvent.load(pendingLivePreview);
      await waitFor(() => {
        expect(screen.getByAltText("Live generation preview")).not.toHaveClass("retained-image--pending");
        expect(document.querySelector('img[src*="filename=previous.png"]')).not.toBeInTheDocument();
      });
      expect(document.querySelector(viewMode === "canvas" ? ".widget-output-image--live" : ".preview-stage--live")).toBeInTheDocument();
      if (viewMode === "canvas") {
        const livePreviewButton = screen.getByAltText("Live generation preview").closest("[role='button']");
        expect(livePreviewButton).toHaveAttribute("aria-disabled", "true");
        expect(livePreviewButton).toHaveAttribute("tabindex", "-1");
        expect(screen.queryByRole("button", { name: /download result a image/i })).not.toBeInTheDocument();
      }

      terminalProgressRequest.resolve(
        jsonResponse({
          job_id: "job-current",
          status: "completed",
          value: 1,
          max: 1,
          current_node: null,
          message: "Execution completed",
          live_preview_sequence: 1,
          live_preview: {
            sequence: 1,
            kind: "image",
            mime_type: "image/png",
            data_url: null,
            node_id: "7",
            prompt_id: "job-current",
            target_node_ids: ["9"],
          },
        }),
      );

      await waitFor(() => {
        expect(
          fetchMock.mock.calls.some(([request]) =>
            String(request).endsWith("/api/jobs/job-current/progress?since_preview_sequence=1"),
          ),
        ).toBe(true);
      }, { timeout: 2500 });

      secondResultRequest.resolve(
        jsonResponse({
          job_id: "job-current",
          status: "completed",
          outputs: [
            {
              node_id: "9",
              output: {
                images: [
                  {
                    view_url:
                      "/api/jobs/job-current/outputs/view?filename=current.png&subfolder=&type=output",
                  },
                ],
              },
            },
          ],
          error: null,
        }),
      );

      const pendingFinalImage = await screen.findByAltText("Generated workflow output");
      expect(pendingFinalImage).toHaveClass("retained-image--pending");
      expect(document.querySelector(`img[src="${livePreviewDataUrl}"]`)).toHaveAttribute("aria-hidden", "true");

      fireEvent.load(pendingFinalImage);
      await waitFor(() => {
        expect(screen.getByAltText("Generated workflow output")).not.toHaveClass("retained-image--pending");
        expect(document.querySelector(`img[src="${livePreviewDataUrl}"]`)).not.toBeInTheDocument();
      });
      expect(document.querySelector(viewMode === "canvas" ? ".widget-output-image--live" : ".preview-stage--live")).not.toBeInTheDocument();
      if (viewMode === "canvas") {
        expect(screen.getByRole("button", { name: /open generated workflow output full-screen/i })).toHaveAttribute("aria-disabled", "false");
        expect(screen.getByRole("button", { name: /download result a image/i })).toBeInTheDocument();
      }
    },
  );

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
    expect(screen.getByText("Starting this workflow...")).toBeInTheDocument();
    // Run stays enabled while a run is pending: another press queues a run.
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeDisabled();
    expect(screen.queryByText("This workflow is already running.")).not.toBeInTheDocument();
    expect(document.querySelector(".primary-button .spin")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-pending",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
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

    expect(await screen.findByRole("dialog", { name: "Setting up workflow" })).toBeInTheDocument();
    expect(screen.getByText(/Resolving custom-node dependencies|Checking the isolated runner/)).toBeInTheDocument();
    expect(screen.getByText("Install workflow extras")).toBeInTheDocument();
    expect(screen.getByText("Set up workflow files")).toBeInTheDocument();
    expect(screen.getByText("Start workflow engine")).toBeInTheDocument();
    expect(screen.getByText("Verify workflow extras")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-prepared",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    });
  });

  it("keeps source-build logs hidden until Developer details is opened", async () => {
    const pendingStatus = {
      ...workflowStatus,
      workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
      install: {
        status: "pending",
        user_facing_message: "Not started",
        requires_preparation: true,
      },
    };
    const failedStatus = {
      ...pendingStatus,
      install: {
        status: "failed",
        user_facing_message: "Noofy could not prepare this workflow.",
        last_error: "A custom-node dependency could not be installed.",
        last_error_code: "dependency_source_build_failed",
        developer_details_available: true,
        requires_preparation: true,
      },
    };
    let statusCalls = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        statusCalls += 1;
        return Promise.resolve(jsonResponse(statusCalls > 1 ? failedStatus : pendingStatus));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            workflow_id: "text_to_image_v0",
            valid: false,
            missing_models: [],
            errors: ["A custom-node dependency could not be installed."],
            error_category: "workflow_preparation",
            error_code: "dependency_source_build_failed",
            developer_details: { developer_details_available: true },
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/install-state/developer-details")) {
        return Promise.resolve(
          jsonResponse({
            workflow_id: "text_to_image_v0",
            developer_details: {
              last_error_code: "dependency_source_build_failed",
              transaction_id: "install-test",
              diagnostic_logs: {
                "dependency-install/uv-install.log": "build backend failed while compiling groundingdino-py",
              },
            },
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const dialog = await screen.findByRole("dialog", { name: "Couldn't set up this workflow" });
    expect(screen.getByText("A custom-node dependency could not be installed.")).toBeInTheDocument();
    expect(screen.queryByText(/build backend failed/)).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/workflows/text_to_image_v0/install-state/developer-details"),
      ),
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Developer details" }));

    expect(await screen.findByText(/build backend failed while compiling groundingdino-py/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide developer details" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Couldn't set up this workflow" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
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

    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    expect(screen.getByText("Starting this workflow...")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-ready",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    });
  });

  it("never shows the preparation dialog for workflows that do not require preparation", async () => {
    const runRequest = deferred<Response>();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(
          jsonResponse({
            ...workflowStatus,
            // Even if a passive install state lingers, requires_preparation
            // false means no preparation will ever run for this workflow.
            install: { status: "pending", user_facing_message: "Not started", requires_preparation: false },
          }),
        );
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") return runRequest.promise;
      if (url.endsWith("/api/jobs/job-warm/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-warm",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-warm/result")) {
        return Promise.resolve(jsonResponse({ job_id: "job-warm", status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    expect(screen.getByText("Starting this workflow...")).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-warm",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
  });

  it("keeps the preparation dialog closed for passive install states until preparation actually starts", async () => {
    const runRequest = deferred<Response>();
    const pendingStatus = {
      ...workflowStatus,
      workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
      install: { status: "pending", user_facing_message: "Not started", requires_preparation: true },
    };
    const preparingStatus = {
      ...pendingStatus,
      install: {
        status: "resolving_dependencies",
        user_facing_message: "Resolving custom-node dependencies.",
        requires_preparation: true,
      },
    };
    let statusCalls = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        statusCalls += 1;
        return Promise.resolve(jsonResponse(statusCalls > 2 ? preparingStatus : pendingStatus));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") return runRequest.promise;
      if (url.endsWith("/api/jobs/job-passive/progress")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-passive",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-passive/result")) {
        return Promise.resolve(jsonResponse({ job_id: "job-passive", status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    // The passive "pending" state means preparation has not started: no dialog.
    await waitFor(() => expect(statusCalls).toBeGreaterThan(1));
    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();

    // Once the backend reports active preparation, the dialog opens.
    expect(await screen.findByRole("dialog", { name: "Setting up workflow" }, { timeout: 3000 })).toBeInTheDocument();

    runRequest.resolve(
      jsonResponse({
        job_id: "job-passive",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    });
  });

  it("does not re-open the preparation dialog on the next run after preparation completed", async () => {
    const firstRunRequest = deferred<Response>();
    const preparingStatus = {
      ...workflowStatus,
      workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
      install: {
        status: "materializing_model_view",
        user_facing_message: "Preparing model access.",
      },
    };
    const readyStatus = {
      ...workflowStatus,
      workflow: { ...workflowStatus.workflow, custom_node_count: 1 },
      install: { status: "ready", user_facing_message: "Ready" },
    };
    // Preparation completes while the first run request is in flight, but the
    // request resolves before another preparation poll can observe "ready".
    let backendReady = false;
    let statusCalls = 0;
    let runCalls = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        statusCalls += 1;
        return Promise.resolve(jsonResponse(backendReady ? readyStatus : preparingStatus));
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
        runCalls += 1;
        if (runCalls === 1) return firstRunRequest.promise;
        return Promise.resolve(
          jsonResponse({
            job_id: "job-second",
            workflow_id: "text_to_image_v0",
            engine: "comfyui",
            status: "queued",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-first/progress") || url.endsWith("/api/jobs/job-second/progress")) {
        const jobId = url.includes("job-first") ? "job-first" : "job-second";
        return Promise.resolve(
          jsonResponse({
            job_id: jobId,
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          }),
        );
      }
      if (url.endsWith("/api/jobs/job-first/result") || url.endsWith("/api/jobs/job-second/result")) {
        const jobId = url.includes("job-first") ? "job-first" : "job-second";
        return Promise.resolve(jsonResponse({ job_id: jobId, status: "completed", outputs: [], error: null }));
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    // First run: preparation is genuinely in progress, so the dialog shows.
    expect(await screen.findByRole("dialog", { name: "Setting up workflow" })).toBeInTheDocument();

    backendReady = true;
    const statusCallsBeforeResolve = statusCalls;
    firstRunRequest.resolve(
      jsonResponse({
        job_id: "job-first",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      }),
    );

    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    });
    // The page must re-read workflow status after the run so the stored
    // install state lands on the backend's final "ready".
    await waitFor(() => expect(statusCalls).toBeGreaterThan(statusCallsBeforeResolve));

    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    // Second run: the backend already reports ready, so the preparation
    // dialog must not re-open from the earlier non-ready snapshot.
    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
    expect(await screen.findByText("Result ready.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Setting up workflow" })).not.toBeInTheDocument();
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

    const dialog = await screen.findByRole("dialog", { name: "Run stopped" });
    expect(within(dialog).queryByText(rootCause)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/runtime\.workspace/)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "View logs" }));
    expect(await within(dialog).findByText(/Node package dependency could not be resolved\./)).toBeInTheDocument();
    expect(await within(dialog).findByText(/runtime\.workspace/)).toBeInTheDocument();
  });

  it("shows a friendly missing image dialog without raw engine text as the primary message", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      imageInputPackageData,
      {
        workflow_id: "text_to_image_v0",
        valid: false,
        missing_models: [],
        errors: ["A required workflow input is missing."],
        user_errors: [
          {
            code: "missing_required_input",
            title: "Missing image",
            message: "A required workflow input is missing.",
            user_message: "Please add an image before running this workflow.",
            severity: "user_fixable",
            control_id: "ctrl-source-image",
            input_id: "source_image",
            input_type: "image",
            developer_details: {
              node_id: "267:276",
              input_name: "image",
              engine_error: "prompt_outputs_failed_validation",
              raw_validation_markers: "LoadImage NoneType endswith",
            },
          },
        ],
      },
      (url) => {
        if (url.endsWith("/api/logs?limit=200")) {
          return jsonResponse({
            events: [
              diagnosticEvent("comfyui.adapter", "prompt_outputs_failed_validation"),
              diagnosticEvent("engine.service", "Workflow run blocked by dashboard input validation"),
            ],
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const dialog = await screen.findByRole("dialog", { name: "Missing image" });
    expect(within(dialog).getByText("Please add an image before running this workflow.")).toBeInTheDocument();
    expect(screen.queryByText("The workflow is not ready")).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/400 Bad Request/i)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/NoneType/i)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/267:276/i)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /developer details/i }));
    expect(within(dialog).getByText(/NoneType/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /view logs/i }));
    expect(await within(dialog).findByRole("heading", { name: "ComfyUI engine logs" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /copy details/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("prompt_outputs_failed_validation");

    fireEvent.click(within(dialog).getByRole("button", { name: /fix input/i }));
    const target = document.querySelector('[data-dashboard-control-id="ctrl-source-image"]') as HTMLElement;
    expect(target).toBeTruthy();
    expect(target.classList.contains("dashboard-control-target--highlight")).toBe(true);
    expect(scrollIntoView).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
  });

  it("shows a friendly missing prompt dialog", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, {
      workflow_id: "text_to_image_v0",
      valid: false,
      missing_models: [],
      errors: ["A required workflow input is missing."],
      user_errors: [
        {
          code: "missing_required_input",
          title: "Missing prompt",
          message: "A required workflow input is missing.",
          user_message: "Please enter text before running this workflow.",
          severity: "user_fixable",
          control_id: "prompt",
          input_id: "prompt",
          input_type: "textarea",
          developer_details: {
            node_id: "6",
            input_name: "text",
          },
        },
      ],
    });

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.change(screen.getByRole("textbox", { name: /prompt/i }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const dialog = await screen.findByRole("dialog", { name: "Missing prompt" });
    expect(within(dialog).getByText("Please enter text before running this workflow.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/400 Bad Request/i)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/NoneType/i)).not.toBeInTheDocument();
  });

  it("opens Missing Models after run validation reports missing required models", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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

      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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

    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    expect(screen.queryByText("This workflow needs required models")).not.toBeInTheDocument();

    fireEvent.click(runButton);

    expect(await screen.findByRole("dialog", { name: "Missing Models" })).toBeInTheDocument();
    expect(screen.getAllByText(/v1-5-pruned-emaonly-fp16\.safetensors/).length).toBeGreaterThan(0);
  });

  it("uses the completed model download summary to clear stale run-page missing model state", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
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

      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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
            status: "completed",
            user_facing_message: "Model download check finished.",
            current_model_filename: "v1-5-pruned-emaonly-fp16.safetensors",
            current_model_index: 1,
            total_models: 1,
            bytes_downloaded: 1024,
            total_bytes: 1024,
            percent: 100,
            speed_bytes_per_second: null,
            models: [],
            model_summary: downloadedModelSummary,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    const dialog = await screen.findByRole("dialog", { name: "Missing Models" });
    expect(within(dialog).getByText("v1-5-pruned-emaonly-fp16.safetensors")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Download Missing Models" }));

    expect(await within(dialog).findByText("Text to Image has all required model files available.")).toBeInTheDocument();
    expect(within(dialog).getByText("Available")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Download Missing Models" })).toBeDisabled();
  });

  it("allows Run while required models are checking, then opens Missing Models if backend validation fails", async () => {
    const checkingModelSummary = {
      ...missingModelSummary,
      missing_count: 0,
      ready_to_run: false,
      models: missingModelSummary.models.map((model) => ({
        ...model,
        status: "checking",
        status_label: "Checking",
        message: "Noofy is checking whether this model is already available locally.",
      })),
    };
    let modelSummaryRequests = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      {
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
      },
      (url) => {
        if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
          modelSummaryRequests += 1;
          return jsonResponse(modelSummaryRequests === 1 ? checkingModelSummary : missingModelSummary);
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/validate")) {
          return jsonResponse({
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
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    expect(screen.queryByText("This workflow needs required models")).not.toBeInTheDocument();

    fireEvent.click(runButton);

    expect(await screen.findByRole("dialog", { name: "Missing Models" })).toBeInTheDocument();
    expect(screen.getAllByText(/v1-5-pruned-emaonly-fp16\.safetensors/).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/text_to_image_v0/run",
      expect.objectContaining({ method: "POST" }),
    );
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

    expect(await screen.findByText("ComfyUI is not responding")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeDisabled();
  });

  it("keeps Run available while the managed engine is starting", async () => {
    vi.useFakeTimers();
    let runtimeCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, () => {
      runtimeCalls += 1;
      return runtimeCalls === 1 ? engineStartingRuntimeState.runtime : readyRuntime;
    });

    render(
      // lastCheckedAt must be fresh at render time (the fixture captures
      // Date.now() at module load): a stale timestamp lets the page's silent
      // mount refresh through, which would consume the "starting" response.
      <RuntimeStatusProvider initialRuntimeState={{ ...engineStartingRuntimeState, lastCheckedAt: Date.now() }}>
        <WorkflowRunPage workflowId="text_to_image_v0" onBack={vi.fn()} onNavigate={vi.fn()} />
      </RuntimeStatusProvider>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getAllByText("Starting").length).toBeGreaterThan(0);
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    expect(runButton).toBeEnabled();
    expect(screen.queryByText("Starting ComfyUI")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(runButton).toBeEnabled();
    expect(screen.queryByText("Starting ComfyUI")).not.toBeInTheDocument();
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

    expect((await screen.findAllByText("Run stopped")).length).toBeGreaterThan(0);
    const dialog = await screen.findByRole("dialog", { name: "Run stopped" });
    expect(within(dialog).getByText("The run stopped before it finished.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/ComfyUI execution failed/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Started best-effort job memory sampling/)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Developer details" }));
    expect(within(dialog).getByText(/model failed/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "View logs" }));
    expect(await within(dialog).findByText(/ComfyUI execution failed/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Started best-effort job memory sampling/)).toBeInTheDocument();
  });

  it("shows a friendly runtime memory error and loads logs only on request", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) return Promise.resolve(jsonResponse(workflowStatus));
      if (url.endsWith("/api/workflows/text_to_image_v0/validate")) return Promise.resolve(jsonResponse(validWorkflow));
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-memory",
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        }));
      }
      if (url.endsWith("/api/jobs/job-memory/progress")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-memory",
          status: "failed",
          value: null,
          max: null,
          current_node: "1",
          message: "Not enough memory to run this workflow",
          error_code: "memory_oom",
        }));
      }
      if (url.endsWith("/api/jobs/job-memory/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-memory",
          status: "failed",
          outputs: [],
          error: "Not enough memory to run this workflow",
          error_code: "memory_oom",
          user_message: "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
          memory_requirement: {
            required_vram_mb: 22712,
            total_vram_mb: 22589,
            available_vram_mb: 8,
            required_ram_mb: null,
            total_ram_mb: null,
            available_ram_mb: null,
            capacity_exceeded: true,
            freeing_memory_may_help: false,
            source: "runtime_oom",
            confidence: "high",
          },
          developer_details: {
            original_error: "CUDA out of memory. Tried to allocate 1.19 GiB.",
            workflow_id: "text_to_image_v0",
            job_id: "job-memory",
          },
        }));
      }
      if (url.endsWith("/api/jobs/job-memory/logs?limit=200")) {
        return Promise.resolve(jsonResponse({
          events: [
            { ...diagnosticEvent("comfyui.adapter", "ComfyUI execution failed", { message: "CUDA out of memory" }), job_id: "job-memory" },
            { ...diagnosticEvent("memory_governor", "Recorded local workflow memory observation"), job_id: "job-memory" },
          ],
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();
    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    const dialog = await screen.findByRole("dialog", { name: "Not enough memory to run this workflow" });
    expect(within(dialog).getByText("Your computer does not have enough available RAM or GPU memory for this workflow right now.")).toBeInTheDocument();
    expect(within(dialog).getByText("GPU memory: about 22.2 GB required; about 22.1 GB was available to this workflow.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Closing apps or freeing memory is unlikely/)).toBeInTheDocument();
    expect(within(dialog).queryByText("Close other apps that may be using memory.")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Free memory, then try again.")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("heading", { name: "ComfyUI engine logs" })).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/CUDA out of memory/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/jobs/job-memory/logs?limit=200"))).toBe(false);

    fireEvent.click(within(dialog).getByRole("button", { name: "Developer details" }));
    expect(within(dialog).getByText(/CUDA out of memory/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("heading", { name: "ComfyUI engine logs" })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "View logs" }));
    expect(await within(dialog).findByRole("heading", { name: "ComfyUI engine logs" })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Noofy logs" })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Copy developer report" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("CUDA out of memory");
  });

  it("keeps a memory waiting state quiet and tracks the queue id", async () => {
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

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes("/api/jobs/workflow-run-queue-text_to_image_v0-1/progress"),
        ),
      ).toBe(true);
    });
    expect(screen.queryByText("Waiting for the GPU")).not.toBeInTheDocument();
    expect(screen.queryByText("This workflow is waiting until the current GPU work finishes.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
    expect(screen.getByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();
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
      state: "memory_cleanup_failed",
      status: "blocked_by_memory",
      title: "Not enough memory to run this workflow",
      message: "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
    },
    {
      state: "blocked_external_pressure",
      status: "blocked_by_memory",
      title: "Not enough memory to run this workflow",
      message: "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
    },
    {
      state: "blocked_exceeds_capacity",
      status: "blocked_by_memory",
      title: "Not enough memory to run this workflow",
      message: "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
    },
    {
      state: "blocked_unattributed_pressure",
      status: "blocked_by_memory",
      title: "Not enough memory to run this workflow",
      message: "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
    },
  ])("shows distinct memory copy for $state", async ({ state, status, title, message }) => {
    const exceedsCapacity = state === "blocked_exceeds_capacity";
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
      memory_requirement: status === "blocked_by_memory" ? {
        required_vram_mb: exceedsCapacity ? 26_000 : 10_000,
        total_vram_mb: 24_000,
        available_vram_mb: 5_000,
        required_ram_mb: state === "memory_cleanup_failed" ? 9_367 : null,
        total_ram_mb: state === "memory_cleanup_failed" ? 15_782 : 64_000,
        available_ram_mb: state === "memory_cleanup_failed" ? 5_352 : 50_000,
        capacity_exceeded: exceedsCapacity,
        freeing_memory_may_help: !exceedsCapacity,
        source: "memory_governor_decision",
        confidence: "high",
      } : null,
      memory_status: {
        state,
        message: "Not enough memory is available for this run.",
        risk_level: "high",
        queue_id: status === "queued_pending_memory" ? `memory-${state}` : null,
        can_cancel: status === "queued_pending_memory",
        can_retry_after_cleanup: state === "retrying_after_memory_cleanup",
      },
    }, (url) => {
      if (status === "blocked_by_memory" && url.endsWith(`/api/jobs/memory-${state}/logs?limit=200`)) {
        return jsonResponse({
          events: [
            {
              ...diagnosticEvent("runs.orchestrator", "Workflow run blocked by workflow runner memory admission", {
                required_free_ram_mb: 8_000,
                final_free_ram_mb: 5_600,
                blocking_constraints: ["ram_below_required"],
              }),
              job_id: `memory-${state}`,
            },
          ],
        });
      }
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
    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
    if (status === "blocked_by_memory") {
      const dialog = screen.getByRole("dialog", { name: title });
      expect(within(dialog).getByText(
        exceedsCapacity
          ? "GPU memory: about 25.4 GB required; about 4.88 GB free when checked (23.4 GB total)."
          : "GPU memory: about 9.77 GB required; about 4.88 GB free when checked (23.4 GB total).",
      )).toBeInTheDocument();
      if (state === "memory_cleanup_failed") {
        expect(within(dialog).getByText(
          "RAM: about 9.15 GB required; about 5.23 GB free when checked (15.4 GB total).",
        )).toBeInTheDocument();
      }
      if (exceedsCapacity) {
        expect(within(dialog).queryByText("Close other apps that may be using memory.")).not.toBeInTheDocument();
      } else {
        expect(within(dialog).getByText("Close other apps that may be using memory.")).toBeInTheDocument();
      }
      expect(screen.queryByText(/test_memory_state/)).not.toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: "Developer details" }));
      expect(within(dialog).getByText(/test_memory_state/)).toBeInTheDocument();
      fireEvent.click(within(dialog).getByRole("button", { name: "View logs" }));
      expect(await within(dialog).findByText(
        "ComfyUI did not run because Noofy stopped this workflow before submission.",
      )).toBeInTheDocument();
      expect(within(dialog).getByText(/Workflow run blocked by workflow runner memory admission/)).toBeInTheDocument();
    } else {
      expect(screen.getByText(/test_memory_state/)).toBeInTheDocument();
    }
    expect(screen.getByText("Developer details")).toBeInTheDocument();
  });

  it.each(["canvas", "classic"])(
    "keeps %s preparation quiet while memory is being unloaded",
    async (viewMode) => {
      window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode }));
      mockConfiguredDashboardFetch(
        fetchMock,
        readyRuntime,
        configuredPackageData,
        {
          job_id: "workflow-run-queue-preparing",
          queue_id: "workflow-run-queue-preparing",
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "queued_pending_memory",
          message: "Preparing this workflow to run.",
          memory_status: {
            state: "preparing_run",
            message: "Preparing this workflow to run.",
            risk_level: "unknown",
            queue_id: "workflow-run-queue-preparing",
            can_cancel: true,
            can_retry_after_cleanup: false,
          },
        },
        (url) => {
          if (!url.endsWith("/api/jobs/workflow-run-queue-preparing/progress")) return undefined;
          return jsonResponse({
            job_id: "workflow-run-queue-preparing",
            queue_id: "workflow-run-queue-preparing",
            status: "queued_pending_memory",
            value: null,
            max: null,
            current_node: null,
            message: "Noofy is unloading the previous workflow before starting this one.",
            memory_status: {
              state: "unloading_previous_workflow",
              message: "Noofy is unloading the previous workflow before starting this one.",
              risk_level: "high",
              queue_id: "workflow-run-queue-preparing",
              can_cancel: true,
              can_retry_after_cleanup: true,
            },
          });
        },
      );

      renderRunPage();
      await waitForReadyStatus();
      const runButton = await screen.findByRole("button", { name: /run workflow/i });
      await waitFor(() => expect(runButton).toBeEnabled());
      fireEvent.click(runButton);

      await waitFor(() => {
        expect(fetchMock.mock.calls.some(([input]) =>
          String(input).endsWith("/api/jobs/workflow-run-queue-preparing/progress"),
        )).toBe(true);
      });
      expect(screen.queryByText("Preparing run")).not.toBeInTheDocument();
      expect(screen.queryByText(
        "Noofy is unloading the previous workflow before starting this one.",
      )).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
      expect(screen.getByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();
    },
  );

  it("keeps managed engine startup quiet after Run is pressed", async () => {
    mockConfiguredDashboardFetch(
      fetchMock,
      engineStartingRuntimeState.runtime,
      configuredPackageData,
      {
        job_id: "workflow-run-queue-starting-engine",
        queue_id: "workflow-run-queue-starting-engine",
        workflow_id: "text_to_image_v0",
        engine: "noofy",
        status: "queued_pending_memory",
        message: "Starting the local ComfyUI engine before this run.",
        memory_status: {
          state: "starting_engine",
          message: "Starting the local ComfyUI engine before this run.",
          risk_level: "unknown",
          queue_id: "workflow-run-queue-starting-engine",
          can_cancel: true,
          can_retry_after_cleanup: false,
        },
      },
      (url) => {
        if (!url.endsWith("/api/jobs/workflow-run-queue-starting-engine/progress")) return undefined;
        return jsonResponse({
          job_id: "workflow-run-queue-starting-engine",
          queue_id: "workflow-run-queue-starting-engine",
          status: "queued_pending_memory",
          value: null,
          max: null,
          current_node: null,
          message: "Starting the local ComfyUI engine before this run.",
          memory_status: {
            state: "starting_engine",
            message: "Starting the local ComfyUI engine before this run.",
            risk_level: "unknown",
            queue_id: "workflow-run-queue-starting-engine",
            can_cancel: true,
            can_retry_after_cleanup: false,
          },
        });
      },
    );

    renderRunPage({}, engineStartingRuntimeState);

    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/jobs/workflow-run-queue-starting-engine/progress"),
      )).toBe(true);
    });
    expect(screen.queryByText("Starting engine")).not.toBeInTheDocument();
    expect(screen.queryByText("Starting ComfyUI")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
    expect(screen.getByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();
  });

  it("shows a queued capacity failure with its memory evidence", async () => {
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      {
        job_id: "workflow-run-queue-capacity",
        queue_id: "workflow-run-queue-capacity",
        workflow_id: "text_to_image_v0",
        engine: "noofy",
        status: "queued_pending_memory",
        message: "Preparing this workflow to run.",
        memory_status: {
          state: "preparing_run",
          message: "Preparing this workflow to run.",
          risk_level: "unknown",
          queue_id: "workflow-run-queue-capacity",
          can_cancel: true,
          can_retry_after_cleanup: false,
        },
      },
      (url) => {
        if (!url.endsWith("/api/jobs/workflow-run-queue-capacity/progress")) return undefined;
        return jsonResponse({
          job_id: "workflow-run-queue-capacity",
          queue_id: "workflow-run-queue-capacity",
          status: "failed",
          value: null,
          max: null,
          current_node: null,
          message: "This workflow needs more memory than this machine can provide.",
          error_code: "insufficient_memory",
          memory_requirement: {
            required_vram_mb: 20_000,
            total_vram_mb: 12_000,
            available_vram_mb: 10_000,
            required_ram_mb: 20_000,
            total_ram_mb: 16_000,
            available_ram_mb: 12_000,
            capacity_exceeded: true,
            freeing_memory_may_help: false,
            source: "memory_governor_decision",
            confidence: "high",
          },
          memory_status: {
            state: "blocked_exceeds_capacity",
            message: "This workflow needs more memory than this machine can provide.",
            risk_level: "high",
            queue_id: "workflow-run-queue-capacity",
            can_cancel: false,
            can_retry_after_cleanup: false,
          },
          developer_details: {
            memory_decision: { reason_code: "estimated_peak_exceeds_capacity" },
          },
        });
      },
    );

    renderRunPage();
    await waitForReadyStatus();
    const runButton = await screen.findByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    const dialog = await screen.findByRole("dialog", {
      name: "Not enough memory to run this workflow",
    });
    expect(within(dialog).getByText(
      "GPU memory: about 19.5 GB required; about 9.77 GB free when checked (11.7 GB total).",
    )).toBeInTheDocument();
    expect(within(dialog).getByText(/more memory than this machine has/)).toBeInTheDocument();
  });

  it("recovers a queued capacity failure that finished while the workflow page was away", async () => {
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/jobs/workflow-run-queue-away/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "workflow-run-queue-away",
          queue_id: "workflow-run-queue-away",
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "blocked_by_memory",
          message: "This workflow needs more memory than this machine can provide.",
          error_code: "insufficient_memory",
          memory_requirement: {
            required_vram_mb: 20_000,
            total_vram_mb: 12_000,
            available_vram_mb: 10_000,
            required_ram_mb: 20_000,
            total_ram_mb: 16_000,
            available_ram_mb: 12_000,
            capacity_exceeded: true,
            freeing_memory_may_help: false,
            source: "memory_governor_decision",
            confidence: "high",
          },
          memory_status: {
            state: "blocked_exceeds_capacity",
            message: "This workflow needs more memory than this machine can provide.",
            risk_level: "high",
            queue_id: "workflow-run-queue-away",
            can_cancel: false,
            can_retry_after_cleanup: false,
          },
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "failed",
      activeJobProgress: {
        job_id: "workflow-run-queue-away",
        queue_id: "workflow-run-queue-away",
        status: "failed",
        value: null,
        max: null,
        current_node: null,
        message: "This workflow needs more memory than this machine can provide.",
        error_code: "insufficient_memory",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    const dialog = await screen.findByRole("dialog", {
      name: "Not enough memory to run this workflow",
    });
    expect(within(dialog).getAllByText(/19.5 GB required/)).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/workflow-run-queue-away/result",
      expect.anything(),
    );
    fireEvent.click(within(dialog).getByText("Close"));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    });
  });

  it("recovers a preparation cancellation that finished while the workflow page was away", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/jobs/workflow-run-queue-canceled-away/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "workflow-run-queue-canceled-away",
          queue_id: "workflow-run-queue-canceled-away",
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "canceled",
          message: "Workflow run canceled.",
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "canceled",
      activeJobProgress: {
        job_id: "workflow-run-queue-canceled-away",
        queue_id: "workflow-run-queue-canceled-away",
        status: "canceled",
        value: null,
        max: null,
        current_node: null,
        message: "Workflow run canceled.",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    expect(await screen.findByText("Run canceled.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.queryByRole("progressbar", { name: /workflow progress/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/workflow-run-queue-canceled-away/result",
      expect.anything(),
    );
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

  it("queues a second run of the same workflow silently, without any warning copy", async () => {
    let runCalls = 0;
    let firstProgressCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, () => {
      runCalls += 1;
      if (runCalls === 1) {
        return {
          job_id: "job-first",
          workflow_id: "text_to_image_v0",
          engine: "noofy",
          status: "running",
          message: "Runner is ready.",
          memory_status: {
            state: "ready_reusing_runner",
            message: "Runner is ready.",
            risk_level: "low",
            queue_id: null,
            can_cancel: true,
            can_retry_after_cleanup: false,
          },
        };
      }
      return {
        job_id: "queue-second",
        queue_id: "queue-second",
        workflow_id: "text_to_image_v0",
        engine: "noofy",
        status: "queued_pending_memory",
        message: "This run is queued and will start when the current run finishes.",
        memory_status: {
          state: "queued_behind_active_run",
          message: "This run is queued and will start when the current run finishes.",
        },
      };
    }, (url) => {
      if (url.endsWith("/api/jobs/job-first/progress")) {
        firstProgressCalls += 1;
        return jsonResponse({
          job_id: "job-first",
          status: firstProgressCalls > 1 ? "completed" : "running",
          value: firstProgressCalls > 1 ? 4 : 1,
          max: 4,
          current_node: null,
          message: firstProgressCalls > 1 ? "Execution completed" : "Generating image...",
        });
      }
      if (url.endsWith("/api/jobs/job-first/result")) {
        return jsonResponse({ job_id: "job-first", status: "completed", outputs: [], error: null });
      }
      if (url.endsWith("/api/jobs/queue-second/progress")) {
        return jsonResponse({
          job_id: "queue-second",
          queue_id: "queue-second",
          status: "queued_pending_memory",
          value: null,
          max: null,
          current_node: null,
          message: "This run is queued and will start when the current run finishes.",
        });
      }
      return undefined;
    });

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);

    // The warm-runner run keeps Run enabled, allowing a second click.
    expect(await screen.findByText("Models loaded")).toBeInTheDocument();
    await waitFor(() => expect(runButton).toBeEnabled());
    fireEvent.click(runButton);
    await waitFor(() => expect(runCalls).toBe(2));

    // Queueing behind the workflow's own active run is silent: only the
    // progress/queue indicators communicate it, never a warning.
    expect(await screen.findByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();
    expect(screen.queryByText("Waiting for another run")).not.toBeInTheDocument();
    expect(screen.queryByText(/handoff/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Run queued")).not.toBeInTheDocument();
    expect(screen.queryByText("Developer details")).not.toBeInTheDocument();
    expect(screen.queryByText("This run is queued and will start when the current run finishes.")).not.toBeInTheDocument();

    // The queue state remains silent after the first run finishes and the
    // queued run becomes the current tracked handle.
    await waitFor(() => expect(firstProgressCalls).toBeGreaterThan(1), { timeout: 3000 });
    await waitFor(() => {
      expect(document.querySelector(".preview-panel .panel-heading p")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("This run is queued and will start when the current run finishes.")).not.toBeInTheDocument();
  });

  it("keeps Run enabled during an active run so repeated presses queue more runs", async () => {
    let runCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, () => {
      runCalls += 1;
      if (runCalls === 1) {
        return {
          job_id: "job-first",
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "running",
          message: "Generating image...",
        };
      }
      return {
        job_id: `queue-${runCalls}`,
        queue_id: `queue-${runCalls}`,
        workflow_id: "text_to_image_v0",
        engine: "noofy",
        status: "queued_pending_memory",
        message: "This run is queued and will start when the current run finishes.",
        memory_status: {
          state: "queued_behind_active_run",
          message: "This run is queued and will start when the current run finishes.",
        },
      };
    }, (url) => {
      if (url.endsWith("/api/jobs/job-first/progress")) {
        return jsonResponse({
          job_id: "job-first",
          status: "running",
          value: 1,
          max: 4,
          current_node: null,
          message: "Generating image...",
        });
      }
      const queueProgressMatch = url.match(/\/api\/jobs\/(queue-\d+)\/progress$/);
      if (queueProgressMatch) {
        return jsonResponse({
          job_id: queueProgressMatch[1],
          queue_id: queueProgressMatch[1],
          status: "queued_pending_memory",
          value: null,
          max: null,
          current_node: null,
          message: "This run is queued and will start when the current run finishes.",
        });
      }
      return undefined;
    });

    renderRunPage();

    await waitForReadyStatus();
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    await waitFor(() => expect(runButton).toBeEnabled());

    // Three presses: the first starts the run, the next two queue behind it.
    fireEvent.click(runButton);
    await waitFor(() => expect(runCalls).toBe(1));
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    await waitFor(() => expect(runCalls).toBe(2));
    expect(runButton).toBeEnabled();
    fireEvent.click(runButton);
    await waitFor(() => expect(runCalls).toBe(3));
    expect(runButton).toBeEnabled();

    // Current run plus the two queued presses show up in the queue indicator.
    expect(await screen.findByTitle("3 runs remaining")).toBeInTheDocument();

    // Queueing behind the workflow's own runs stays silent and never blocks.
    expect(screen.queryByText("This workflow is already running.")).not.toBeInTheDocument();
    expect(screen.queryByText("Waiting for another run")).not.toBeInTheDocument();
    expect(screen.queryByText(/handoff/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Run queued")).not.toBeInTheDocument();

    // The active run's progress stays on screen instead of flashing back to a
    // preparing/starting state when more runs are queued.
    expect(screen.queryByText("Starting this workflow...")).not.toBeInTheDocument();
    expect(screen.queryByText(/preparing workflow/i)).not.toBeInTheDocument();
  });

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

    expect(await screen.findByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();
    expect(screen.queryByText("Making room for this workflow")).not.toBeInTheDocument();
    expect(screen.queryByText("Noofy is freeing memory before this run starts.")).not.toBeInTheDocument();
    expect(screen.queryByText("Noofy is unloading models from the previous run so this one can start.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));

    await waitFor(() => expect(screen.queryByText("Making room for this workflow")).not.toBeInTheDocument());
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
    expect(screen.queryByText("This workflow is already running.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));

    await waitFor(() => expect(cancelCalls).toBe(1));
    await waitFor(() => {
      expect(screen.queryByRole("progressbar", { name: /workflow progress/i })).not.toBeInTheDocument();
    });
  });

  it("enables cancellation from active progress when the local job object is unavailable", async () => {
    let cancelCalls = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      null,
      (url, init) => {
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/active-and-queued")) {
          return jsonResponse({ active_count: 1, queued_count: 0, total_count: 1 });
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/runs/cancel-active-and-queued") && init?.method === "POST") {
          cancelCalls += 1;
          return jsonResponse({
            canceled_active_count: 1,
            canceled_queued_count: 0,
            already_terminal_count: 0,
            failed_to_cancel_count: 0,
          });
        }
        return undefined;
      },
    );

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "running",
      activeJobProgress: {
        job_id: "job-progress-only",
        status: "running",
        value: 2,
        max: 10,
        current_node: "3",
        message: "Loading models...",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    expect((await screen.findAllByText("Working")).length).toBeGreaterThan(0);
    const cancelButton = await screen.findByRole("button", { name: "Cancel run" });
    await waitFor(() => expect(cancelButton).toBeEnabled());
    expect(screen.queryByText("This workflow is already running.")).not.toBeInTheDocument();
    fireEvent.click(cancelButton);

    await waitFor(() => expect(cancelCalls).toBe(1));
  });

  it("submits Batch Count runs with identical captured payloads when the seed is fixed", async () => {
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
          validation: { seed_mode: "fixed" },
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

  it("advances the seed after each generation when the seed mode is increment", async () => {
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
          validation: { seed_mode: "increment" },
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
    const runBodies: Array<{ inputs: Record<string, unknown> }> = [];
    let runIndex = 0;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      seededPackageData,
      (init?: RequestInit) => {
        runIndex += 1;
        runBodies.push(JSON.parse(String(init?.body)));
        return {
          job_id: `job-seed-${runIndex}`,
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        const match = url.match(/\/api\/jobs\/(job-seed-\d+)\/progress$/);
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
    expect(runBodies.map((body) => body.inputs.seed)).toEqual([123, 124, 125, 126]);
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
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));

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
    expect(screen.queryByRole("dialog", { name: "Run stopped" })).not.toBeInTheDocument();
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

  it("edits a canvas widget title inline and saves the dashboard label without touching the widget body", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    await screen.findByRole("button", { name: /workflow options/i });
    const promptCell = document.querySelector('[data-dashboard-control-id="prompt"]') as HTMLElement;
    expect(promptCell).toBeInTheDocument();

    fireEvent.doubleClick(within(promptCell).getByRole("heading", { name: "Prompt" }));

    const titleInput = within(promptCell).getByRole("textbox", { name: "Edit widget name" });
    fireEvent.change(titleInput, { target: { value: "Creative prompt" } });

    expect(titleInput).toHaveValue("Creative prompt");
    expect(promptCell.querySelector("textarea")).toHaveValue("a lake");

    fireEvent.blur(titleInput);

    expect(within(promptCell).getByRole("heading", { name: "Creative prompt" })).toBeInTheDocument();
    await waitFor(() => {
      const dashboardSave = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/dashboard") &&
          (init as RequestInit | undefined)?.method === "PUT" &&
          JSON.parse(((init as RequestInit | undefined)?.body as string | undefined) ?? "{}")
            .dashboard?.sections?.[0]?.controls?.[0]?.label === "Creative prompt",
      );
      expect(dashboardSave).toBeDefined();
    });
  });

  it("orders workflow toolbar controls as batch, run, cancel, then options", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    await screen.findByRole("button", { name: /workflow options/i });
    const batch = screen.getByRole("spinbutton", { name: "Batch count" }).closest(".canvas-batch-count-stepper") as HTMLElement;
    const runButton = screen.getByRole("button", { name: /run workflow/i });
    const cancelButton = screen.getByRole("button", { name: "Cancel run" });
    const optionsButton = screen.getByRole("button", { name: /workflow options/i });

    expect(batch.compareDocumentPosition(runButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(runButton.compareDocumentPosition(cancelButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(cancelButton.compareDocumentPosition(optionsButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("lets grouped multiline text controls expand inside their group widget", async () => {
    const groupedPackageData = {
      ...configuredPackageData,
      inputs: [
        configuredPackageData.inputs[0],
        {
          id: "negative_prompt",
          label: "Negative Prompt",
          control: "textarea",
          binding: { node_id: "7", input_name: "text" },
          default: "low quality, blurry",
          validation: {},
        },
      ],
      outputs: [],
      dashboard: {
        ...configuredPackageData.dashboard,
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
                description: "Describe what you want to create.",
              },
              {
                id: "negative_prompt",
                type: "textarea",
                label: "Negative Prompt",
                input_id: "negative_prompt",
                description: "Describe what you don't want to create.",
              },
            ],
            groups: [
              {
                id: "prompt-group",
                title: "Prompt + Negative Prompt",
                control_ids: ["prompt", "negative_prompt"],
                layout: { x: 0, y: 0, w: 24, h: 18 },
              },
            ],
          },
        ],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, groupedPackageData);

    renderRunPage();

    expect(await screen.findByText("Prompt + Negative Prompt")).toBeInTheDocument();
    const groupedTextareaControls = document.querySelectorAll(".canvas-widget-group__control--textarea");

    expect(groupedTextareaControls).toHaveLength(2);
    expect(within(groupedTextareaControls[0] as HTMLElement).getByRole("heading", { name: "Prompt" })).toBeInTheDocument();
    expect(within(groupedTextareaControls[0] as HTMLElement).getByText("Describe what you want to create.")).toBeInTheDocument();
    expect(within(groupedTextareaControls[1] as HTMLElement).getByRole("heading", { name: "Negative Prompt" })).toBeInTheDocument();
    expect(within(groupedTextareaControls[1] as HTMLElement).getByText("Describe what you don't want to create.")).toBeInTheDocument();
    groupedTextareaControls.forEach((control) => {
      expect(control.querySelector(".canvas-widget-textarea")).toBeInTheDocument();
      expect(control).toHaveStyle({ flexGrow: "6" });
    });
    expect(canvasCss).toMatch(/\.canvas-widget-group__control\s*{[^}]*flex-basis:\s*0;[^}]*flex-shrink:\s*1;/);
    expect(canvasCss).toMatch(/\.layout-canvas-widget--compact \.canvas-widget-group__control\s*{[^}]*overflow:\s*auto;/);
    expect(canvasCss).toMatch(/\.layout-canvas-widget--inline-toggle \.layout-canvas-widget__header\s*{[^}]*align-items:\s*center;/);
    expect(canvasCss).toMatch(/\.canvas-widget-inline-toggle \.canvas-widget-toggle\s*{[^}]*--canvas-toggle-track-width:\s*44px;/);
  });

  it("keeps the run canvas within the visible workspace width", () => {
    expect(canvasCss).toMatch(
      /\.canvas-dashboard \.layout-canvas__surface\s*{[^}]*min-width:\s*0;/,
    );
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

  it("renders generated text from PreviewAny in a text output widget", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const textPackageData = {
      ...configuredPackageData,
      outputs: [{ id: "text", label: "Text", node_id: "4", type: "text", kind: "text" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-text",
            type: "display_text",
            label: "Text result",
            output_id: "text",
            layout: { x: 0, y: 0, w: 12, h: 8 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, textPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-text", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-text/progress")) {
        return Promise.resolve(jsonResponse({ job_id: "job-text", status: "completed", value: 1, max: 1, message: "Execution completed" }));
      }
      if (url.endsWith("/api/jobs/job-text/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-text",
          status: "completed",
          outputs: [{ node_id: "4", output: { text: ["Hello from Gemma."] } }],
          error: null,
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPage();

    expect(await screen.findByText("Text result")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /auto save/i })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));
    expect(await screen.findByText("Hello from Gemma.")).toBeInTheDocument();
    expect(document.querySelector(".widget-output-text pre")).toHaveTextContent("Hello from Gemma.");
    expect(canvasCss).toMatch(/\.widget-output-text pre\s*{[^}]*user-select:\s*text;/);
    fireEvent.click(screen.getByRole("button", { name: /copy text result output/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Hello from Gemma."));
    expect(screen.queryByRole("button", { name: /save.*gallery/i })).not.toBeInTheDocument();
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
    expect(await screen.findByText("MP4 · 4 KB")).toHaveClass("widget-output-video__footer-meta");
    expect(screen.queryByText("clip.mp4")).not.toBeInTheDocument();
    expect(document.querySelector("video")).toHaveAttribute(
      "src",
      "/api/jobs/job-video/outputs/view?filename=clip.mp4&subfolder=&type=output",
    );
    expect(screen.queryByText("MP4 · 1280 × 720 · 24 fps · 4 KB · 0:03")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save to Gallery" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fullscreen" })).toBeInTheDocument();
  });

  it("renders a generated 3D model in the display_3d result widget", async () => {
    const threeDPackageData = {
      ...configuredPackageData,
      outputs: [{ id: "model", label: "3D model", node_id: "30", type: "3d", kind: "3d" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-3d",
            type: "display_3d",
            label: "3D result",
            output_id: "model",
            layout: { x: 0, y: 0, w: 16, h: 14 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, threeDPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-three-d", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-three-d/progress")) {
        return Promise.resolve(jsonResponse({ job_id: "job-three-d", status: "completed", value: 1, max: 1, message: "Execution completed" }));
      }
      if (url.endsWith("/api/jobs/job-three-d/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-three-d",
          status: "completed",
          outputs: [{
            node_id: "30",
            output: {
              "3d": [{
                filename: "preview3d_abc123.usdz",
                kind: "3d",
                type: "3d",
                output_type: "output",
                mime_type: "application/octet-stream",
                size: null,
                url: "/api/jobs/job-three-d/outputs/view?filename=preview3d_abc123.usdz&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));
    await waitFor(() => expect(document.querySelector(".widget-output-three-d .three-d-viewer")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Preview 3D model" })).not.toBeInTheDocument();
  });

  it("loads a completed 3D result when returning from the dashboard builder after the run finished", async () => {
    const threeDPackageData = {
      ...configuredPackageData,
      outputs: [{ id: "model", label: "3D model", node_id: "30", type: "3d", kind: "3d" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-3d",
            type: "display_3d",
            label: "3D result",
            output_id: "model",
            layout: { x: 0, y: 0, w: 16, h: 14 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, threeDPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/jobs/job-builder-finished/result")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-builder-finished",
          status: "completed",
          outputs: [{
            node_id: "30",
            output: {
              "3d": [{
                filename: "preview3d_finished.spz",
                kind: "3d",
                type: "3d",
                output_type: "output",
                mime_type: "application/octet-stream",
                size: 3072,
                url: "/api/jobs/job-builder-finished/outputs/view?filename=preview3d_finished.spz&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        }));
      }
      return configuredFetch(input, init);
    });

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "completed",
      activeJobProgress: {
        job_id: "job-builder-finished",
        status: "completed",
        value: 1,
        max: 1,
        current_node: null,
        message: "Execution completed",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    await waitFor(() => expect(document.querySelector(".widget-output-three-d .three-d-viewer")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs/job-builder-finished/result", expect.anything());
    expect(screen.queryByRole("button", { name: "Preview 3D model" })).not.toBeInTheDocument();
  });

  it("keeps polling when recovered terminal progress has a non-terminal result payload", async () => {
    let resultCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url) => {
      if (url.endsWith("/api/jobs/job-history-lag/progress")) {
        return jsonResponse({
          job_id: "job-history-lag",
          status: "completed",
          value: 1,
          max: 1,
          current_node: null,
          message: "Execution completed",
        });
      }
      if (url.endsWith("/api/jobs/job-history-lag/result")) {
        resultCalls += 1;
        if (resultCalls === 1) {
          return jsonResponse({
            job_id: "job-history-lag",
            status: "running",
            outputs: [],
            error: null,
          });
        }
        return jsonResponse({
          job_id: "job-history-lag",
          status: "completed",
          outputs: [{
            node_id: "9",
            output: {
              images: [{
                view_url: "/api/jobs/job-history-lag/outputs/view?filename=history-lag.png&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        });
      }
      return undefined;
    });

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "completed",
      activeJobProgress: {
        job_id: "job-history-lag",
        status: "completed",
        value: 1,
        max: 1,
        current_node: null,
        message: "Execution completed",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    await waitFor(() => {
      expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
        "src",
        "/api/jobs/job-history-lag/outputs/view?filename=history-lag.png&subfolder=&type=output",
      );
    }, { timeout: 3000 });
    expect(resultCalls).toBeGreaterThan(1);
  });

  it("keeps polling when recovered completed result is still empty", async () => {
    let resultCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url) => {
      if (url.endsWith("/api/jobs/job-empty-completed/progress")) {
        return jsonResponse({
          job_id: "job-empty-completed",
          status: "completed",
          value: 1,
          max: 1,
          current_node: null,
          message: "Execution completed",
        });
      }
      if (url.endsWith("/api/jobs/job-empty-completed/result")) {
        resultCalls += 1;
        if (resultCalls === 1) {
          return jsonResponse({
            job_id: "job-empty-completed",
            status: "completed",
            outputs: [],
            error: null,
          });
        }
        return jsonResponse({
          job_id: "job-empty-completed",
          status: "completed",
          outputs: [{
            node_id: "9",
            output: {
              images: [{
                view_url: "/api/jobs/job-empty-completed/outputs/view?filename=empty-then-ready.png&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        });
      }
      return undefined;
    });

    renderRunPageWithWorkflowRuntime({
      activeJobId: null,
      activeJobStatus: "completed",
      activeJobProgress: {
        job_id: "job-empty-completed",
        status: "completed",
        value: 1,
        max: 1,
        current_node: null,
        message: "Execution completed",
      },
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });

    await waitFor(() => {
      expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
        "src",
        "/api/jobs/job-empty-completed/outputs/view?filename=empty-then-ready.png&subfolder=&type=output",
      );
    }, { timeout: 3000 });
    expect(resultCalls).toBeGreaterThan(1);
  });

  it("recovers a completed output from the stored workflow job handle after returning by tab", async () => {
    const recoveredImagePackageData = {
      ...configuredPackageData,
      outputs: [{ id: "image", label: "Result", node_id: "46", type: "image", kind: "image" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [
            {
              id: "prompt",
              type: "textarea",
              label: "Prompt",
              input_id: "prompt",
              layout: { x: 0, y: 0, w: 12, h: 6 },
            },
            {
              id: "result-image",
              type: "display_image",
              label: "Result",
              output_id: "image",
              layout: { x: 12, y: 0, w: 20, h: 16 },
            },
          ],
        }],
      },
    };
    window.sessionStorage.setItem("noofy.workflowRunHandles.v1", JSON.stringify({
      handles: {
        text_to_image_v0: {
          workflowId: "text_to_image_v0",
          jobId: "job-stored-complete",
          queueId: "workflow-run-queue-stored",
          status: "completed",
          updatedAt: Date.now(),
        },
      },
    }));
    let resultCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, recoveredImagePackageData, null, (url) => {
      if (url.endsWith("/api/jobs/job-stored-complete/result")) {
        resultCalls += 1;
        return jsonResponse({
          job_id: "job-stored-complete",
          queue_id: "workflow-run-queue-stored",
          status: "completed",
          outputs: [{
            node_id: "46",
            output: {
              images: [{
                filename: "Anima_00007_.png",
                kind: "image",
                type: "image",
                mime_type: "image/png",
                view_url: "/api/jobs/job-stored-complete/outputs/view?filename=Anima_00007_.png&subfolder=&type=output",
              }],
            },
          }],
          error: null,
        });
      }
      return undefined;
    });

    renderRunPage();

    expect(await screen.findByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-stored-complete/outputs/view?filename=Anima_00007_.png&subfolder=&type=output",
    );
    expect(resultCalls).toBe(1);
  });

  it("does not drop a recovered result when React replays the recovery effect", async () => {
    window.sessionStorage.setItem("noofy.workflowRunHandles.v1", JSON.stringify({
      handles: {
        text_to_image_v0: {
          workflowId: "text_to_image_v0",
          jobId: "job-strict-recovery",
          queueId: "workflow-run-queue-strict",
          status: "completed",
          updatedAt: Date.now(),
        },
      },
    }));
    const resultRequest = deferred<Response>();
    let resultCalls = 0;
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url) => {
      if (url.endsWith("/api/jobs/job-strict-recovery/result")) {
        resultCalls += 1;
        return resultRequest.promise;
      }
      return undefined;
    });

    render(
      <StrictMode>
        <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
          <ResourceStatusProvider initialSnapshot={resourceSnapshot} skipInitialRefresh>
            <WorkflowRunPage
              workflowId="text_to_image_v0"
              onBack={vi.fn()}
              onNavigate={vi.fn()}
            />
          </ResourceStatusProvider>
        </RuntimeStatusProvider>
      </StrictMode>,
    );

    await waitFor(() => expect(resultCalls).toBe(1));
    await act(async () => {
      resultRequest.resolve(jsonResponse({
        job_id: "job-strict-recovery",
        queue_id: "workflow-run-queue-strict",
        status: "completed",
        outputs: [{
          node_id: "9",
          output: {
            images: [{
              view_url: "/api/jobs/job-strict-recovery/outputs/view?filename=strict-recovery.png&subfolder=&type=output",
            }],
          },
        }],
        error: null,
      }));
    });

    expect(await screen.findByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-strict-recovery/outputs/view?filename=strict-recovery.png&subfolder=&type=output",
    );
  });

  it("persists a completed result when the result fetch resolves after leaving the run page", async () => {
    const resultRequest = deferred<Response>();
    let resultRequested = false;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      { job_id: "job-away-result", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" },
      (url) => {
        if (url.endsWith("/api/jobs/job-away-result/progress")) {
          return jsonResponse({
            job_id: "job-away-result",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          });
        }
        if (url.endsWith("/api/jobs/job-away-result/result")) {
          resultRequested = true;
          return resultRequest.promise;
        }
        return undefined;
      },
    );

    function RouteHarness() {
      const [route, setRoute] = useState<"workflow" | "models">("workflow");
      return (
        <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
          <ResourceStatusProvider initialSnapshot={resourceSnapshot} skipInitialRefresh>
            <WorkflowTabsProvider>
              {route === "workflow" ? (
                <WorkflowRunPage
                  workflowId="text_to_image_v0"
                  onBack={vi.fn()}
                  onNavigate={() => setRoute("models")}
                />
              ) : (
                <button type="button" onClick={() => setRoute("workflow")}>Return to workflow</button>
              )}
            </WorkflowTabsProvider>
          </ResourceStatusProvider>
        </RuntimeStatusProvider>
      );
    }

    render(<RouteHarness />);

    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));
    await waitFor(() => expect(resultRequested).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Models" }));
    await act(async () => {
      resultRequest.resolve(jsonResponse({
        job_id: "job-away-result",
        status: "completed",
        outputs: [{
          node_id: "9",
          output: {
            images: [{
              view_url: "/api/jobs/job-away-result/outputs/view?filename=route-return.png&subfolder=&type=output",
            }],
          },
        }],
        error: null,
      }));
    });

    fireEvent.click(await screen.findByRole("button", { name: "Return to workflow" }));

    expect(await screen.findByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-away-result/outputs/view?filename=route-return.png&subfolder=&type=output",
    );
  });

  it("routes unmatched 3D live previews into the single display_3d canvas widget", async () => {
    const resultRequest = deferred<Response>();
    const livePreviewDataUrl = "data:image/png;base64,M2QtbGl2ZS1wcmV2aWV3";
    const threeDPackageData = {
      ...configuredPackageData,
      inputs: [],
      outputs: [{ id: "model", label: "3D model", node_id: "30", type: "3d", kind: "3d" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-3d",
            type: "display_3d",
            label: "3D result",
            output_id: "model",
            layout: { x: 0, y: 0, w: 16, h: 14 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, threeDPackageData);
    const configuredFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/workflows/text_to_image_v0/run")) {
        return Promise.resolve(jsonResponse({ job_id: "job-three-d-preview", workflow_id: "text_to_image_v0", engine: "comfyui", status: "queued" }));
      }
      if (url.endsWith("/api/jobs/job-three-d-preview/progress")) {
        return Promise.resolve(jsonResponse({
          job_id: "job-three-d-preview",
          status: "completed",
          value: 1,
          max: 1,
          message: "Execution completed",
          live_preview_sequence: 1,
          live_preview: {
            sequence: 1,
            kind: "image",
            mime_type: "image/png",
            data_url: livePreviewDataUrl,
            node_id: "7",
            prompt_id: "job-three-d-preview",
            target_node_ids: ["7"],
          },
        }));
      }
      if (url.endsWith("/api/jobs/job-three-d-preview/result")) {
        return resultRequest.promise;
      }
      return configuredFetch(input, init);
    });

    renderRunPage();

    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));
    const livePreview = await screen.findByAltText("Live generation preview");
    expect(livePreview).toHaveAttribute("src", livePreviewDataUrl);
    expect(livePreview.closest(".widget-output-image--live")).toBeInTheDocument();
    expect(document.querySelector(".canvas-live-preview")).not.toBeInTheDocument();

    resultRequest.resolve(jsonResponse({
      job_id: "job-three-d-preview",
      status: "completed",
      outputs: [{
        node_id: "30",
        output: {
          "3d": [{
            filename: "preview3d_abc123.glb",
            kind: "3d",
            type: "3d",
            output_type: "output",
            mime_type: "model/gltf-binary",
            size: null,
            url: "/api/jobs/job-three-d-preview/outputs/view?filename=preview3d_abc123.glb&subfolder=&type=output",
          }],
        },
      }],
      error: null,
    }));
    await waitFor(() => expect(document.querySelector(".widget-output-three-d .three-d-viewer")).toBeInTheDocument());
  });

  it("shows 3D-specific empty-state copy for display_3d canvas widgets", async () => {
    const threeDPackageData = {
      ...configuredPackageData,
      inputs: [],
      outputs: [{ id: "model", label: "3D model", node_id: "30", type: "3d", kind: "3d" }],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [{
          id: "main",
          title: "Main",
          controls: [{
            id: "result-3d",
            type: "display_3d",
            label: "3D result",
            output_id: "model",
            layout: { x: 0, y: 0, w: 16, h: 14 },
          }],
        }],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, threeDPackageData);

    renderRunPage();

    expect(await screen.findByRole("main", { name: /workflow dashboard canvas/i })).toBeInTheDocument();
    expect(screen.getByText("Your generated 3D model will appear here.")).toBeInTheDocument();
    expect(screen.queryByText("Your generated image will appear here.")).not.toBeInTheDocument();
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
    expect(screen.queryByText("ComfyUI is not responding")).not.toBeInTheDocument();
  });

  it("opens Missing Models from canvas run when required models are missing", async () => {
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
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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
    expect(runButton).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(runButton);

    const missingModelsDialog = await screen.findByRole("dialog", { name: "Missing Models" });
    expect(missingModelsDialog).toBeInTheDocument();
    expect(
      Array.from(missingModelsDialog.querySelector(".required-models-modal")?.children ?? []).map(
        (child) => child.className,
      ),
    ).toEqual([
      "required-models-modal__header",
      "required-models-modal__body",
      "required-models-modal__footer",
    ]);
    expect(within(missingModelsDialog).getByText("Checkpoint")).toBeInTheDocument();
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

  it("shows manual-download model details after run validation reports a model blocker", async () => {
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
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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

    const runButton = await screen.findByRole("button", { name: "Run workflow" });
    expect(runButton).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(runButton);

    const missingModelsDialog = await screen.findByRole("dialog", { name: "Missing Models" });
    expect(within(missingModelsDialog).getByText("Needs manual download")).toBeInTheDocument();
    expect(within(missingModelsDialog).getByText("Download Missing Models")).toBeDisabled();
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
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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

    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));

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
      if (url.endsWith("/api/workflows/text_to_image_v0/run") && init?.method === "POST") {
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

    fireEvent.click(await screen.findByRole("button", { name: "Run workflow" }));

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
    expect(screen.getByRole("status")).toHaveTextContent("Loading workflow");
    expect(screen.queryByText("Loading saved inputs")).not.toBeInTheDocument();

    // Flush the fire-and-forget /api/resources fetch so its trailing setState lands inside act().
    await act(async () => {});
  });

  it.each(["canvas", "classic"] as const)(
    "does not flash package defaults while saved values load in %s view",
    async (viewMode) => {
      window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode }));
      const userStateResponse = deferred<Response>();
      const dashboardVersion = dashboardUserStateVersionForTest(configuredPackageData);
      mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url, init) => {
        if (url.endsWith("/api/workflows/text_to_image_v0/user-state") && (!init?.method || init.method === "GET")) {
          return userStateResponse.promise;
        }
        return undefined;
      });

      renderRunPage();

      expect(await screen.findByText("Loading saved inputs")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("a lake")).not.toBeInTheDocument();
      expect(screen.queryByText("a lake")).not.toBeInTheDocument();
      if (viewMode === "classic") {
        expect(screen.getByRole("button", { name: "Run workflow" })).toBeDisabled();
      } else {
        expect(screen.queryByRole("button", { name: "Run workflow" })).not.toBeInTheDocument();
        expect(document.querySelector(".workflow-values-loading--compact")).not.toBeInTheDocument();
      }

      await act(async () => {
        userStateResponse.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: dashboardVersion,
          values: { prompt: "my saved prompt" },
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        }));
      });

      expect(await screen.findByDisplayValue("my saved prompt")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("a lake")).not.toBeInTheDocument();
      expect(screen.queryByText("Loading saved inputs")).not.toBeInTheDocument();
    },
  );

  it("renders saved dashboard values without waiting for model checks or validation", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const modelSummaryResponse = deferred<Response>();
    const validationResponse = deferred<Response>();
    const dashboardVersion = dashboardUserStateVersionForTest(configuredPackageData);
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, configuredPackageData, null, (url, init) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/model-summary")) {
        return modelSummaryResponse.promise;
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/validate") && init?.method === "POST") {
        return validationResponse.promise;
      }
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state") && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: dashboardVersion,
          values: { prompt: "saved prompt before model checks" },
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        });
      }
      return undefined;
    });

    renderRunPage();

    expect(await screen.findByDisplayValue("saved prompt before model checks")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("a lake")).not.toBeInTheDocument();
    expect(screen.queryByText("Loading saved inputs")).not.toBeInTheDocument();

    const runButton = screen.getByRole("button", { name: "Run workflow" });
    expect(runButton).toBeEnabled();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/workflows/text_to_image_v0/model-summary"))).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/api/workflows/text_to_image_v0/validate"))).toBe(false);
  });

  it("ignores a slower workflow load after switching to another workflow", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const slowPackageResponse = deferred<Response>();
    const packageFor = (workflowId: string, workflowName: string, defaultValue: string) => ({
      ...configuredPackageData,
      metadata: {
        ...configuredPackageData.metadata,
        id: workflowId,
        name: workflowName,
      },
      inputs: configuredPackageData.inputs.map((input) => ({
        ...input,
        default: defaultValue,
      })),
    });
    const fastPackage = packageFor("fast-workflow", "Fast Workflow", "fast default");

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/slow-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "slow-workflow",
          workflow: { ...workflowStatus.workflow, id: "slow-workflow", name: "Slow Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/fast-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "fast-workflow",
          workflow: { ...workflowStatus.workflow, id: "fast-workflow", name: "Fast Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/slow-workflow/package")) return slowPackageResponse.promise;
      if (url.endsWith("/api/workflows/fast-workflow/package")) {
        return Promise.resolve(jsonResponse(fastPackage));
      }
      if (url.endsWith("/model-summary")) {
        const id = url.includes("slow-workflow") ? "slow-workflow" : "fast-workflow";
        return Promise.resolve(jsonResponse({ ...readyModelSummary, workflow_id: id }));
      }
      if (url.endsWith("/validate")) {
        const id = url.includes("slow-workflow") ? "slow-workflow" : "fast-workflow";
        return Promise.resolve(jsonResponse({ ...validWorkflow, workflow_id: id }));
      }
      if (url.endsWith("/runs/active-and-queued")) {
        return Promise.resolve(jsonResponse({ active_count: 0, queued_count: 0, total_count: 0 }));
      }
      if (url.endsWith("/api/workflows/fast-workflow/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "fast-workflow",
          dashboard_version: dashboardUserStateVersionForTest(fastPackage),
          values: { prompt: "fast saved value" },
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const onWorkflowNameChange = vi.fn();
    const page = (workflowId: string) => (
      <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
        <WorkflowRunPage workflowId={workflowId} onBack={vi.fn()} onNavigate={vi.fn()} onWorkflowNameChange={onWorkflowNameChange} />
      </RuntimeStatusProvider>
    );
    const { rerender } = render(page("slow-workflow"));
    rerender(page("fast-workflow"));

    expect(await screen.findByDisplayValue("fast saved value")).toBeInTheDocument();
    expect(onWorkflowNameChange).toHaveBeenCalledWith("Fast Workflow");

    await act(async () => {
      slowPackageResponse.resolve(
        jsonResponse(packageFor("slow-workflow", "Slow Workflow", "slow default")),
      );
    });

    await waitFor(() => {
      expect(screen.getByDisplayValue("fast saved value")).toBeInTheDocument();
      expect(onWorkflowNameChange).not.toHaveBeenCalledWith("Slow Workflow");
      expect(screen.queryByDisplayValue("slow default")).not.toBeInTheDocument();
    });
  });

  it("hides the previous dashboard until the switched workflow values are ready", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const nextPackageResponse = deferred<Response>();
    const packageFor = (workflowId: string, workflowName: string, defaultValue: string) => ({
      ...configuredPackageData,
      metadata: {
        ...configuredPackageData.metadata,
        id: workflowId,
        name: workflowName,
      },
      inputs: configuredPackageData.inputs.map((input) => ({
        ...input,
        default: defaultValue,
      })),
    });
    const firstPackage = packageFor("first-workflow", "First Workflow", "first default");
    const nextPackage = packageFor("next-workflow", "Next Workflow", "next default");

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/first-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "first-workflow",
          workflow: { ...workflowStatus.workflow, id: "first-workflow", name: "First Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/next-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "next-workflow",
          workflow: { ...workflowStatus.workflow, id: "next-workflow", name: "Next Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/first-workflow/package")) {
        return Promise.resolve(jsonResponse(firstPackage));
      }
      if (url.endsWith("/api/workflows/next-workflow/package")) return nextPackageResponse.promise;
      if (url.endsWith("/model-summary")) {
        const id = url.includes("first-workflow") ? "first-workflow" : "next-workflow";
        return Promise.resolve(jsonResponse({ ...readyModelSummary, workflow_id: id }));
      }
      if (url.endsWith("/validate")) {
        const id = url.includes("first-workflow") ? "first-workflow" : "next-workflow";
        return Promise.resolve(jsonResponse({ ...validWorkflow, workflow_id: id }));
      }
      if (url.endsWith("/runs/active-and-queued")) {
        return Promise.resolve(jsonResponse({ active_count: 0, queued_count: 0, total_count: 0 }));
      }
      if (url.endsWith("/api/workflows/first-workflow/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "first-workflow",
          dashboard_version: dashboardUserStateVersionForTest(firstPackage),
          values: { prompt: "first saved value" },
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        }));
      }
      if (url.endsWith("/api/workflows/next-workflow/user-state")) {
        return Promise.resolve(jsonResponse({
          schema_version: "1",
          workflow_id: "next-workflow",
          dashboard_version: dashboardUserStateVersionForTest(nextPackage),
          values: { prompt: "next saved value" },
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const page = (workflowId: string) => (
      <RuntimeStatusProvider initialRuntimeState={readyRuntimeState} skipInitialRefresh>
        <WorkflowRunPage workflowId={workflowId} onBack={vi.fn()} onNavigate={vi.fn()} />
      </RuntimeStatusProvider>
    );
    const { rerender } = render(page("first-workflow"));
    expect(await screen.findByDisplayValue("first saved value")).toBeInTheDocument();

    rerender(page("next-workflow"));

    expect(screen.getByText("Loading workflow")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("first saved value")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url, requestInit]) =>
        String(url).endsWith("/api/workflows/next-workflow/user-state")
        && (!requestInit?.method || requestInit.method === "GET"),
      ),
    ).toHaveLength(0);

    await act(async () => {
      nextPackageResponse.resolve(jsonResponse(nextPackage));
    });

    expect(await screen.findByDisplayValue("next saved value")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([url, requestInit]) =>
        String(url).endsWith("/api/workflows/next-workflow/user-state")
        && (!requestInit?.method || requestInit.method === "GET"),
      ),
    ).toHaveLength(1);
  });

  it("keeps a previously loaded workflow tab rendered while its state refreshes in the background", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const firstPackageRefresh = deferred<Response>();
    const firstUserStateRefresh = deferred<Response>();
    let firstPackageRequestCount = 0;
    let firstUserStateRequestCount = 0;
    const packageFor = (workflowId: string, workflowName: string, defaultValue: string) => ({
      ...configuredPackageData,
      metadata: {
        ...configuredPackageData.metadata,
        id: workflowId,
        name: workflowName,
      },
      inputs: configuredPackageData.inputs.map((input) => ({
        ...input,
        default: defaultValue,
      })),
    });
    const stateFor = (workflowId: string, packageData: ReturnType<typeof packageFor>, prompt: string) => ({
      schema_version: "1",
      workflow_id: workflowId,
      dashboard_version: dashboardUserStateVersionForTest(packageData),
      values: { prompt },
      layout_overrides: {},
      presentation_overrides: {},
      output_preferences: {},
    });
    const firstPackage = packageFor("first-workflow", "First Workflow", "first default");
    const nextPackage = packageFor("next-workflow", "Next Workflow", "next default");

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/settings/apis")) return Promise.resolve(jsonResponse(configuredApiSettings));
      if (url.endsWith("/api/workflows/first-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "first-workflow",
          workflow: { ...workflowStatus.workflow, id: "first-workflow", name: "First Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/next-workflow/status")) {
        return Promise.resolve(jsonResponse({
          ...workflowStatus,
          workflow_id: "next-workflow",
          workflow: { ...workflowStatus.workflow, id: "next-workflow", name: "Next Workflow" },
        }));
      }
      if (url.endsWith("/api/workflows/first-workflow/package")) {
        firstPackageRequestCount += 1;
        return firstPackageRequestCount === 1
          ? Promise.resolve(jsonResponse(firstPackage))
          : firstPackageRefresh.promise;
      }
      if (url.endsWith("/api/workflows/next-workflow/package")) {
        return Promise.resolve(jsonResponse(nextPackage));
      }
      if (url.endsWith("/model-summary")) {
        const id = url.includes("first-workflow") ? "first-workflow" : "next-workflow";
        return Promise.resolve(jsonResponse({ ...readyModelSummary, workflow_id: id }));
      }
      if (url.endsWith("/validate")) {
        const id = url.includes("first-workflow") ? "first-workflow" : "next-workflow";
        return Promise.resolve(jsonResponse({ ...validWorkflow, workflow_id: id }));
      }
      if (url.endsWith("/runs/active-and-queued")) {
        return Promise.resolve(jsonResponse({ active_count: 0, queued_count: 0, total_count: 0 }));
      }
      if (url.endsWith("/api/workflows/first-workflow/user-state")) {
        if (method === "PUT") return Promise.resolve(jsonResponse(JSON.parse(String(init?.body))));
        firstUserStateRequestCount += 1;
        return firstUserStateRequestCount === 1
          ? Promise.resolve(jsonResponse(stateFor("first-workflow", firstPackage, "first saved value")))
          : firstUserStateRefresh.promise;
      }
      if (url.endsWith("/api/workflows/next-workflow/user-state")) {
        if (method === "PUT") return Promise.resolve(jsonResponse(JSON.parse(String(init?.body))));
        return Promise.resolve(jsonResponse(stateFor("next-workflow", nextPackage, "next saved value")));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<WorkflowTabSwitchRunHarness />);

    const firstPrompt = await screen.findByDisplayValue("first saved value");
    fireEvent.change(firstPrompt, { target: { value: "first unsaved value" } });
    expect(screen.getByDisplayValue("first unsaved value")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Next Workflow" }));
    expect(await screen.findByDisplayValue("next saved value")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "First Workflow" }));

    expect(screen.getByDisplayValue("first unsaved value")).toBeInTheDocument();
    expect(screen.queryByText("Loading saved inputs")).not.toBeInTheDocument();
    expect(firstPackageRequestCount).toBeGreaterThan(1);
    expect(firstUserStateRequestCount).toBeGreaterThan(1);
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

  it("does not show engine repair guidance for a transient busy runtime status", async () => {
    mockConfiguredDashboardFetch(fetchMock, engineBusyRuntimeState.runtime);

    renderRunPage({}, engineBusyRuntimeState);

    expect(await screen.findByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.getAllByText("Working").length).toBeGreaterThan(0);
    expect(screen.queryByText("ComfyUI is not responding")).not.toBeInTheDocument();
    expect(screen.queryByText("Starting ComfyUI")).not.toBeInTheDocument();
  });

  it("does not show engine repair guidance while workflow progress is active", async () => {
    mockConfiguredDashboardFetch(fetchMock, engineOfflineRuntimeState.runtime);

    renderRunPageWithWorkflowRuntime(
      {
        activeJobId: "job-loading-models",
        activeJobStatus: "running",
        activeJobProgress: {
          job_id: "job-loading-models",
          status: "running",
          value: 1,
          max: 10,
          current_node: "3",
          message: "Loading models...",
        },
        activeJobUpdatedAt: Date.now(),
        handleSource: "job",
        queueId: null,
      },
      {},
      engineOfflineRuntimeState,
    );

    // While progress is active the stale offline status is not trusted, and
    // Run stays enabled so another press can queue behind the active run.
    expect(await screen.findByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.queryByText("ComfyUI is not responding")).not.toBeInTheDocument();
    expect(screen.queryByText("Starting ComfyUI")).not.toBeInTheDocument();
  });

  it("does not show an offline notice over active workflow preparation", async () => {
    mockConfiguredDashboardFetch(fetchMock, readyRuntime);

    renderRunPageWithWorkflowRuntime(
      {
        activeJobId: "workflow-run-queue-preparing",
        activeJobStatus: "queued_pending_memory",
        activeJobProgress: {
          job_id: "workflow-run-queue-preparing",
          status: "queued_pending_memory",
          value: null,
          max: null,
          current_node: null,
          message: "Preparing this workflow to run.",
        },
        activeJobUpdatedAt: Date.now(),
        handleSource: "workflow_run_queue",
        queueId: "workflow-run-queue-preparing",
      },
      {},
      backendOfflineRuntimeState,
    );

    expect(await screen.findByRole("progressbar", { name: "Workflow progress" })).toHaveAttribute("aria-valuenow", "0");
    expect(screen.queryByText("Noofy is offline")).not.toBeInTheDocument();
    expect(screen.getAllByText("Working").length).toBeGreaterThan(0);
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
    expect(screen.getAllByText("Offline").length).toBeGreaterThan(0);
    const dialog = await screen.findByRole("dialog", { name: "Run stopped" });
    expect(within(dialog).queryByRole("heading", { name: "ComfyUI engine logs" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("heading", { name: "Noofy logs" })).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/CUDA out of memory/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Submitting workflow run/)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /copy logs/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("ComfyUI engine logs");
    expect(copied).toContain("Noofy logs");
    expect(copied).toContain("CUDA out of memory");
    expect(copied).toContain("Submitting workflow run");
    expect(copied).not.toContain("Workflow failure report");
    expect(await within(dialog).findByRole("heading", { name: "ComfyUI engine logs" })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Noofy logs" })).toBeInTheDocument();
    expect(within(dialog).getByText(/CUDA out of memory/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Submitting workflow run/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Logs copied" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /copy details/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /copy developer report/i })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "Developer details" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /copy developer report/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    const detailedCopy = writeText.mock.calls[1][0] as string;
    expect(detailedCopy).toContain("Workflow failure report");
    expect(detailedCopy).toContain("ComfyUI engine logs");
    expect(within(dialog).getByRole("button", { name: "Developer report copied" })).toBeInTheDocument();
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

  it("keeps Run enabled after a retryable workflow preparation failure", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/resources")) return Promise.resolve(jsonResponse(resourceSnapshot));
      if (url.endsWith("/api/runtime")) return Promise.resolve(jsonResponse(readyRuntime));
      if (url.endsWith("/api/workflows/text_to_image_v0/status")) {
        return Promise.resolve(
          jsonResponse({
            ...workflowStatus,
            install: {
              status: "failed",
              user_facing_message: "Cannot prepare automatically",
              last_error: "Previous preparation failed.",
            },
            can_prepare: true,
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

    expect(await screen.findByRole("heading", { name: "Inputs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run workflow/i })).toBeEnabled();
    expect(screen.queryByText("This workflow cannot run on this machine")).not.toBeInTheDocument();
  });

  it("renders the classic two-panel dashboard when classic mode is selected", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    expect(await screen.findByRole("heading", { name: "Inputs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Preview" })).toBeInTheDocument();
    const previewPanel = document.querySelector(".preview-panel");
    expect(previewPanel).toBeInTheDocument();
    expect(previewPanel?.querySelector(".mini-status")).not.toBeInTheDocument();
    expect(document.querySelector(".main-workspace--workflow-run-classic")).toBeInTheDocument();
    expect(document.querySelector(".workspace-content--workflow-run-classic")).toBeInTheDocument();
    expect(document.querySelector(".run-workspace--classic")).toBeInTheDocument();
    expect(document.querySelector(".preview-panel--pinned")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /back to home/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Text to Image" })).not.toBeInTheDocument();
    expect(
      screen.queryByText("Describe the image you want, then let Noofy run the local workflow in the background."),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Workflow actions")).toHaveClass("workflow-action-bar--inline");
    expect(screen.getByLabelText("Workflow actions")).toHaveClass("workflow-action-bar--preview-compact");
    expect(screen.getAllByRole("button", { name: /run workflow/i })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /workflow options/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Share / Save as .noofy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export JSON" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check Again" })).not.toBeInTheDocument();
  });

  it("keeps classic pinned preview and loaded media sizing scoped to classic layout", async () => {
    window.localStorage.setItem("noofy.prefs", JSON.stringify({ viewMode: "classic" }));
    const dashboardVersion = dashboardUserStateVersionForTest(allEditableWidgetTypesPackageData);
    const loadedMediaValues = {
      source_image: { source: "package_asset", kind: "image", asset_id: "package-image.png", filename: "package-image.png" },
      mask_image: { source: "package_asset", kind: "image", asset_id: "package-mask.png", filename: "package-mask.png" },
      source_audio: { source: "package_asset", kind: "audio", asset_id: "package-audio.wav", filename: "package-audio.wav" },
      source_video: { source: "package_asset", kind: "video", asset_id: "package-video.mp4", filename: "package-video.mp4" },
      source_file: { source: "package_asset", kind: "file", asset_id: "package-file.json", filename: "package-file.json" },
      source_3d: { source: "package_asset", kind: "3d", asset_id: "package-model.glb", filename: "package-model.glb" },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, allEditableWidgetTypesPackageData, null, (url, init) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state") && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: dashboardVersion,
          values: loadedMediaValues,
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        });
      }
      return undefined;
    });

    renderRunPage();

    expect(await screen.findByAltText("Selected image: package-image.png")).toBeInTheDocument();
    expect(screen.getByAltText("Selected image: package-mask.png")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-image-input--classic.dashboard-image-input--preview")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-audio-input--classic.dashboard-audio-input--selected")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-video-input--classic.dashboard-video-input--selected")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-file-input--classic.dashboard-file-input--selected")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-three-d-input--classic.dashboard-three-d-input--selected")).toBeInTheDocument();
    expect(document.querySelector(".main-workspace--canvas-run")).not.toBeInTheDocument();
  });

  it("defines the classic pinned preview, input scrolling, and media bounds without changing canvas media sizing", () => {
    expect(componentsCss).toMatch(/\.workspace-content--workflow-run-classic\s*{[^}]*width:\s*min\(1500px, 100%\);[^}]*padding:\s*14px 24px 32px;/);
    expect(componentsCss).toMatch(/\.run-workspace--classic\s*{[^}]*gap:\s*26px;[^}]*grid-template-columns:\s*minmax\(360px, 0\.92fr\) minmax\(440px, 1\.08fr\);/);
    expect(componentsCss).toMatch(/\.main-workspace--workflow-run-classic\s*{[^}]*overflow:\s*hidden;/);
    expect(componentsCss).toMatch(/\.workspace-content--workflow-run-classic\s*{[^}]*height:\s*calc\(100dvh - var\(--topbar-height\)\);[^}]*overflow:\s*hidden;/);
    expect(componentsCss).toMatch(/\.run-workspace--classic > \.run-panel\s*{[^}]*height:\s*100%;[^}]*overflow-y:\s*auto;/);
    expect(componentsCss).toMatch(/\.preview-panel--pinned\s*{[^}]*height:\s*100%;[^}]*overflow:\s*hidden;/);
    expect(componentsCss).toMatch(/\.preview-panel--pinned \.preview-stage\s*{[^}]*min-height:\s*0;[^}]*aspect-ratio:\s*auto;/);
    expect(componentsCss).not.toContain(".preview-panel--sticky");
    expect(componentsCss).toMatch(/\.preview-panel__actions\s*{[^}]*justify-content:\s*flex-end;[^}]*flex-wrap:\s*wrap;/);
    expect(componentsCss).toMatch(/\.dashboard-image-input--preview \.dashboard-image-input__surface\s*{[^}]*cursor:\s*zoom-in;/);
    expect(componentsCss).toMatch(/\.dashboard-image-input--classic\.dashboard-image-input--preview \.dashboard-image-input__surface\s*{[^}]*max-height:\s*320px;[^}]*aspect-ratio:\s*16 \/ 10;/);
    expect(componentsCss).toMatch(/\.dashboard-image-input__preview\s*{[^}]*color:\s*transparent;[^}]*font-size:\s*0;/);
    expect(componentsCss).toMatch(/\.dashboard-video-input--classic\.dashboard-video-input--selected \.dashboard-video-input__player\s*{[^}]*aspect-ratio:\s*16 \/ 9;[^}]*object-fit:\s*contain;/);
    expect(componentsCss).toMatch(/\.dashboard-three-d-input--classic\.dashboard-three-d-input--selected \.three-d-viewer\s*{[^}]*grid-template-rows:\s*220px auto auto;/);
    expect(componentsCss).toMatch(/\.dashboard-image-input--canvas \.dashboard-image-input__surface\s*{[^}]*height:\s*100%;[^}]*min-height:\s*0;/);
    expect(componentsCss).toMatch(/\.dashboard-image-input--canvas \.dashboard-image-input__preview\s*{[^}]*max-height:\s*none;/);
  });

  it("switches immediately between Canvas and Classic views and persists the preference", async () => {
    mockConfiguredDashboardFetch(fetchMock);

    renderRunPage();

    const canvasOptionsButton = await screen.findByRole("button", { name: /workflow options/i });
    fireEvent.click(canvasOptionsButton);
    fireEvent.click(screen.getByRole("menuitem", { name: "Switch to Classic view" }));

    expect(await screen.findByRole("heading", { name: "Inputs" })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("noofy.prefs") ?? "{}")).toMatchObject({
      viewMode: "classic",
    });

    fireEvent.click(screen.getByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Switch to Canvas view" }));

    expect(await screen.findByRole("main", { name: /workflow dashboard canvas/i })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("noofy.prefs") ?? "{}")).toMatchObject({
      viewMode: "canvas",
    });
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
    const cancelButton = screen.getByRole("button", { name: "Cancel run" });
    const runButton = screen.getByRole("button", { name: "Run workflow" });
    expect(cancelButton).toBeDisabled();
    expect(cancelButton).toHaveAttribute("title", "Cancel current run");
    expect(cancelButton).toHaveTextContent("");
    expect(runButton).toHaveAttribute("title", "Run workflow");
    expect(runButton).toHaveTextContent("");
    expect(screen.queryByText("Restore dashboard defaults")).not.toBeInTheDocument();

    fireEvent.click(optionsButton);

    expect(screen.getByRole("menu", { name: /workflow options/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /export the noofy workflow/i }));
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
    expect(screen.getByRole("menuitem", { name: /restore dashboard defaults/i })).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu", { name: /workflow options/i })).not.toBeInTheDocument();

    fireEvent.click(optionsButton);
    expect(screen.getByRole("menu", { name: /workflow options/i })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: /workflow options/i })).not.toBeInTheDocument();
  });

  it("marks a loaded below-default widget compact without changing its dimensions", async () => {
    const compactPackage = {
      ...configuredPackageData,
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: configuredPackageData.dashboard.sections.map((section) => ({
          ...section,
          controls: section.controls.map((control) =>
            control.id === "prompt"
              ? { ...control, layout: { x: 0, y: 0, w: 5, h: 4, min_w: 99, min_h: 99 } }
              : control,
          ),
        })),
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, compactPackage);

    renderRunPage();

    const promptCell = (await screen.findByRole("textbox")).closest("article");
    expect(promptCell).toHaveClass("layout-canvas-widget--compact");
    expect(promptCell).toHaveStyle({ width: "15.625%", height: "128px" });
  });

  it.each([1, 2])("renders %s-row toggle widgets inline in the canvas header", async (height) => {
    const compactTogglePackage = {
      ...configuredPackageData,
      inputs: [
        ...configuredPackageData.inputs,
        {
          id: "thinking",
          label: "Thinking",
          control: "toggle",
          binding: { node_id: "42", input_name: "thinking" },
          default: false,
          validation: {},
        },
      ],
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: [
          {
            id: "main",
            title: "Main",
            controls: [
              {
                id: "thinking",
                type: "toggle",
                label: "Thinking",
                input_id: "thinking",
                layout: { x: 0, y: 0, w: 16, h: height },
              },
            ],
          },
        ],
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, compactTogglePackage);

    renderRunPage();

    const checkbox = await screen.findByRole("checkbox", { name: "Off" });
    const toggleCell = document.querySelector('[data-dashboard-control-id="thinking"]') as HTMLElement;
    const header = toggleCell.querySelector(".layout-canvas-widget__header") as HTMLElement;

    expect(toggleCell).toHaveClass("layout-canvas-widget--compact", "layout-canvas-widget--inline-toggle");
    if (height === 1) {
      expect(toggleCell).toHaveClass("layout-canvas-widget--one-row-toggle");
    } else {
      expect(toggleCell).not.toHaveClass("layout-canvas-widget--one-row-toggle");
    }
    expect(within(header).getByRole("checkbox", { name: "Off" })).toBe(checkbox);
    expect(toggleCell.querySelector(".widget-canvas-cell__content")).not.toBeInTheDocument();
    expect(toggleCell).toHaveStyle({ height: `${height * 32}px` });

    fireEvent.click(checkbox);
    expect(await screen.findByRole("checkbox", { name: "On" })).toBeChecked();
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
    fireEvent.click(screen.getByRole("menuitem", { name: /restore dashboard defaults/i }));

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

  it("posts the current dashboard values when exporting Noofy packages from the canvas", async () => {
    const packageWithExportMetadata = {
      ...configuredPackageData,
      metadata: {
        ...configuredPackageData.metadata,
        author: "Canvas Creator",
        website: "https://canvas.example",
        category: "Txt2img",
        tags: ["starter", "canvas"],
        icon: "image",
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, packageWithExportMetadata);
    const createObjectUrl = vi.fn(() => "blob:workflow-noofy");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    renderRunPage();

    const promptInput = await screen.findByRole("textbox");
    fireEvent.change(promptInput, { target: { value: "current noofy prompt" } });
    fireEvent.click(screen.getByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /export the noofy workflow/i }));
    fireEvent.click(screen.getByRole("button", { name: "Export .noofy" }));

    await waitFor(() => {
      const exportCall = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith("/api/workflows/text_to_image_v0/export") &&
        (init as RequestInit | undefined)?.method === "POST",
      );
      expect(exportCall).toBeTruthy();
      const body = JSON.parse(String((exportCall?.[1] as RequestInit).body));
      expect(body.input_values.prompt).toBe("current noofy prompt");
      expect(body.export_metadata).toMatchObject({
        author: "Canvas Creator",
        website: "https://canvas.example",
        category: "Txt2img",
        tags: ["starter", "canvas"],
        icon: "image",
      });
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
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
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
      expect(body.dashboard.sections[0].controls[0].layout).toMatchObject({
        x: 0,
        y: 0,
        w: 16,
        h: 6,
        min_w: 5,
        min_h: 4,
      });
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

  it.each([
    ["canceling", /^cancel$/i],
    ["saving", /save dashboard/i],
  ])("keeps a run output mounted after %s a layout edit", async (_action, actionName) => {
    const terminalProgressRequest = deferred<Response>();
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      configuredPackageData,
      {
        job_id: "job-layout-edit",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      },
      (url) => {
        if (url.endsWith("/api/jobs/job-layout-edit/progress")) {
          return terminalProgressRequest.promise;
        }
        if (url.endsWith("/api/jobs/job-layout-edit/result")) {
          return jsonResponse({
            job_id: "job-layout-edit",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-layout-edit/outputs/view?filename=result.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    await waitForReadyStatus();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByRole("progressbar", { name: /workflow progress/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit dashboard layout/i }));

    terminalProgressRequest.resolve(
      jsonResponse({
        job_id: "job-layout-edit",
        status: "completed",
        value: 2,
        max: 2,
        current_node: null,
        message: "Execution completed",
      }),
    );

    const outputImage = await screen.findByAltText("Generated workflow output");
    fireEvent.load(outputImage);
    await waitFor(() => expect(screen.getByAltText("Generated workflow output")).not.toHaveClass("retained-image--pending"));
    const loadedOutputImage = screen.getByAltText("Generated workflow output");

    fireEvent.click(screen.getByRole("button", { name: actionName }));

    await waitFor(() => expect(screen.getByRole("button", { name: /workflow options/i })).toBeInTheDocument());
    expect(screen.getByAltText("Generated workflow output")).toBe(loadedOutputImage);
    expect(loadedOutputImage).not.toHaveClass("retained-image--pending");
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

  it("ignores package minimums and lets runtime edit mode shrink to the current Noofy minimum", async () => {
    const packageWithCreatorMinimum = {
      ...configuredPackageData,
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: configuredPackageData.dashboard.sections.map((section) => ({
          ...section,
          controls: section.controls.map((control) =>
            control.id === "prompt"
              ? { ...control, layout: { ...control.layout!, min_w: 99, min_h: 99 } }
              : control,
          ),
        })),
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, packageWithCreatorMinimum);

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
    dispatchPointer(window, "pointermove", { clientX: 0, clientY: 0 });
    dispatchPointer(window, "pointerup", { clientX: 0, clientY: 0 });

    expect(screen.getByRole("textbox").closest("article")).toHaveClass("layout-canvas-widget--compact");
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const userStatePut = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/user-state") &&
          (init as RequestInit | undefined)?.method === "PUT" &&
          JSON.parse(String((init as RequestInit).body)).layout_overrides?.prompt,
      );
      expect(userStatePut).toBeDefined();
      const body = JSON.parse((userStatePut![1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 0, y: 0, w: 5, h: 4 });
    });
  });

  it("moves a loaded below-minimum runtime widget without changing its dimensions", async () => {
    const compactPackage = {
      ...configuredPackageData,
      dashboard: {
        ...configuredPackageData.dashboard,
        sections: configuredPackageData.dashboard.sections.map((section) => ({
          ...section,
          controls: section.controls.map((control) =>
            control.id === "prompt"
              ? { ...control, layout: { x: 0, y: 0, w: 3, h: 2, min_w: 99, min_h: 99 } }
              : control,
          ),
        })),
      },
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, compactPackage);

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
    dispatchPointer(promptCell, "pointerdown", { clientX: 60, clientY: 32 });
    dispatchPointer(window, "pointermove", { clientX: 60, clientY: 96 });
    dispatchPointer(window, "pointerup", { clientX: 60, clientY: 96 });

    expect(promptCell).toHaveStyle({ top: "64px", width: "9.375%", height: "64px" });
    fireEvent.click(screen.getByRole("button", { name: /save dashboard/i }));

    await waitFor(() => {
      const userStatePut = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/api/workflows/text_to_image_v0/user-state") &&
          (init as RequestInit | undefined)?.method === "PUT" &&
          JSON.parse(String((init as RequestInit).body)).layout_overrides?.prompt,
      );
      const body = JSON.parse((userStatePut?.[1] as RequestInit).body as string);
      expect(body.layout_overrides.prompt).toEqual({ x: 0, y: 2, w: 3, h: 2 });
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

  it("opens the dashboard builder widget step from package defaults, not current run values", async () => {
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
            defaultValue: "a lake",
            layout: expect.objectContaining({ x: 0, y: 0, w: 16, h: 6 }),
          }),
        ]),
      }),
    );
  });

  it("opens every editable widget type from package defaults, not saved current values", async () => {
    const onEditWidgets = vi.fn();
    const dashboardVersion = dashboardUserStateVersionForTest(allEditableWidgetTypesPackageData);
    const currentValues = {
      title: "runtime title",
      prompt: "runtime prompt",
      steps: 48,
      height: 512,
      random_seed: 9999,
      enabled: true,
      sampler: "dpmpp_2m",
      style_lora: "runtime-style.safetensors",
      source_image: "runtime-image.png",
      mask_image: "runtime-mask.png",
      source_audio: "runtime-audio.wav",
      source_video: "runtime-video.mp4",
      source_file: "runtime-file.json",
      source_3d: "runtime-model.glb",
      note_value: "runtime note",
      hidden_strength: 0.9,
    };
    mockConfiguredDashboardFetch(fetchMock, readyRuntime, allEditableWidgetTypesPackageData, null, (url, init) => {
      if (url.endsWith("/api/workflows/text_to_image_v0/user-state") && (!init?.method || init.method === "GET")) {
        return jsonResponse({
          schema_version: "1",
          workflow_id: "text_to_image_v0",
          dashboard_version: dashboardVersion,
          values: currentValues,
          layout_overrides: {},
          presentation_overrides: {},
          output_preferences: {},
        });
      }
      if (/\/api\/assets\/[^/]+\/metadata$/.test(url)) {
        const assetId = decodeURIComponent(url.split("/api/assets/")[1].replace("/metadata", ""));
        return jsonResponse({
          asset_id: assetId,
          original_filename: assetId,
          content_type: assetId.endsWith(".png")
            ? "image/png"
            : assetId.endsWith(".wav")
              ? "audio/wav"
              : assetId.endsWith(".mp4")
                ? "video/mp4"
                : assetId.endsWith(".glb")
                  ? "model/gltf-binary"
                  : "application/json",
          kind: assetId.endsWith(".png")
            ? "image"
            : assetId.endsWith(".wav")
              ? "audio"
              : assetId.endsWith(".mp4")
                ? "video"
                : assetId.endsWith(".glb")
                  ? "3d"
                  : "file",
        });
      }
      if (/\/api\/assets\/[^/]+$/.test(url)) {
        return binaryResponse("asset", "application/octet-stream");
      }
      return undefined;
    });

    renderRunPage({ onEditWidgets });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/workflows/text_to_image_v0/user-state"))).toBe(true);
    });
    await waitFor(() => {
      expect(screen.getByDisplayValue("runtime prompt")).toBeInTheDocument();
    });
    fireEvent.click(await screen.findByRole("button", { name: /workflow options/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /edit widgets/i }));

    const schema = onEditWidgets.mock.calls[0]?.[0];
    const widgetsById = new Map(
      [...schema.widgets, ...(schema.hiddenWidgets ?? [])].map((widget) => [widget.id, widget]),
    );

    expect(widgetsById.get("title")).toEqual(expect.objectContaining({ widgetType: "string_field", defaultValue: "package title" }));
    expect(widgetsById.get("prompt")).toEqual(expect.objectContaining({ widgetType: "textarea", defaultValue: "package prompt" }));
    expect(widgetsById.get("steps")).toEqual(expect.objectContaining({ widgetType: "int_field", defaultValue: 24 }));
    expect(widgetsById.get("height")).toEqual(expect.objectContaining({ widgetType: "slider", defaultValue: 768, min: 64, max: 2048, step: 64 }));
    expect(widgetsById.get("random_seed")).toEqual(expect.objectContaining({ widgetType: "seed_widget", defaultValue: 1234 }));
    expect(widgetsById.get("enabled")).toEqual(expect.objectContaining({ widgetType: "toggle", defaultValue: false }));
    expect(widgetsById.get("sampler")).toEqual(expect.objectContaining({ widgetType: "select", defaultValue: "euler" }));
    expect(widgetsById.get("style_lora")).toEqual(expect.objectContaining({ widgetType: "lora_loader", defaultValue: "None" }));
    expect(widgetsById.get("source_image")).toEqual(expect.objectContaining({ widgetType: "load_image", defaultValue: "package-image.png" }));
    expect(widgetsById.get("mask_image")).toEqual(expect.objectContaining({ widgetType: "load_image_mask", defaultValue: "package-mask.png" }));
    expect(widgetsById.get("source_audio")).toEqual(expect.objectContaining({ widgetType: "load_audio", defaultValue: "package-audio.wav" }));
    expect(widgetsById.get("source_video")).toEqual(expect.objectContaining({ widgetType: "load_video", defaultValue: "package-video.mp4" }));
    expect(widgetsById.get("source_file")).toEqual(expect.objectContaining({ widgetType: "load_file", defaultValue: "package-file.json" }));
    expect(widgetsById.get("source_3d")).toEqual(expect.objectContaining({ widgetType: "load_3d", defaultValue: "package-model.glb" }));
    expect(widgetsById.get("note-card")).toEqual(expect.objectContaining({ widgetType: "note", defaultValue: "package note" }));
    expect(widgetsById.get("hidden_strength")).toEqual(expect.objectContaining({ widgetType: "slider", defaultValue: 0.35 }));
    expect(widgetsById.get("result-image")).toEqual(expect.objectContaining({ widgetType: "display_image", defaultValue: null }));
    expect(widgetsById.get("result-audio")).toEqual(expect.objectContaining({ widgetType: "display_audio", defaultValue: null }));
    expect(widgetsById.get("result-video")).toEqual(expect.objectContaining({ widgetType: "display_video", defaultValue: null }));
    expect(widgetsById.get("result-file")).toEqual(expect.objectContaining({ widgetType: "display_file", defaultValue: null }));
    expect(widgetsById.get("result-3d")).toEqual(expect.objectContaining({ widgetType: "display_3d", defaultValue: null }));
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
        return imageResponse("input");
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

  it("shows and submits a packaged default image until the user replaces it", async () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
      content_type: "image/png",
      size_bytes: 123,
      sha256: `sha256:${"a".repeat(64)}`,
    };
    const packageData = {
      ...imageInputPackageData,
      inputs: imageInputPackageData.inputs.map((input) => ({
        ...input,
        default: packagedDefault,
        default_pinned: true,
      })),
    };
    const dashboardVersion = dashboardUserStateVersionForTest(packageData);
    let runBody: { inputs?: Record<string, unknown> } | null = null;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      packageData,
      (init?: RequestInit) => {
        runBody = JSON.parse(String(init?.body ?? "{}"));
        return validWorkflow;
      },
      (url, init) => {
        if (url.endsWith("/api/workflows/text_to_image_v0/user-state") && (!init?.method || init.method === "GET")) {
          return jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: dashboardVersion,
            values: { source_image: null },
            layout_overrides: {},
          });
        }
        return undefined;
      },
    );

    renderRunPage();

    expect(await screen.findByAltText("Selected image: starter.png")).toHaveAttribute(
      "src",
      "/api/workflows/text_to_image_v0/inputs/source_image/default-asset?asset_id=input-defaults%2Fstarter.png",
    );
    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));
    await waitFor(() => {
      expect(runBody?.inputs?.source_image).toEqual(packagedDefault);
    });
  });

  it("uses a packaged default image as the before source for output comparison", async () => {
    const packagedDefault = {
      source: "package_asset",
      asset_id: "input-defaults/starter.png",
      kind: "image",
      filename: "starter.png",
      content_type: "image/png",
      size_bytes: 123,
      sha256: `sha256:${"a".repeat(64)}`,
    };
    const packageData = {
      ...imageInputPackageData,
      inputs: imageInputPackageData.inputs.map((input) => ({
        ...input,
        default: packagedDefault,
        default_pinned: true,
      })),
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
                id: "ctrl-source-image",
                type: "load_image",
                label: "Source image",
                input_id: "source_image",
                required: true,
                layout: { x: 0, y: 0, w: 12, h: 6 },
              },
              {
                id: "result",
                type: "display_image",
                label: "Result",
                output_id: "image",
                layout: { x: 12, y: 0, w: 12, h: 8 },
              },
            ],
          },
        ],
      },
    };
    let runBody: { inputs?: Record<string, unknown> } | null = null;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      packageData,
      (init?: RequestInit) => {
        runBody = JSON.parse(String(init?.body ?? "{}"));
        return {
          job_id: "job-package-edit",
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        if (url.endsWith("/api/jobs/job-package-edit/progress")) {
          return jsonResponse({
            job_id: "job-package-edit",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          });
        }
        if (url.endsWith("/api/jobs/job-package-edit/result")) {
          return jsonResponse({
            job_id: "job-package-edit",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-package-edit/outputs/view?filename=edited.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          });
        }
        return undefined;
      },
    );

    const { container } = renderRunPage();
    const packageAssetUrl =
      "/api/workflows/text_to_image_v0/inputs/source_image/default-asset?asset_id=input-defaults%2Fstarter.png";

    expect(await screen.findByAltText("Selected image: starter.png")).toHaveAttribute("src", packageAssetUrl);
    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));

    const slider = await screen.findByRole("slider", { name: /compare original image/i });
    expect(slider).toBeInTheDocument();
    await waitFor(() => {
      expect(runBody).toEqual(
        expect.objectContaining({
          inputs: expect.objectContaining({ source_image: packagedDefault }),
        }),
      );
    });
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute(
      "src",
      "/api/jobs/job-package-edit/outputs/view?filename=edited.png&subfolder=&type=output",
    );
    const beforeImage = container.querySelector(".image-comparison-slider__image--before");
    expect(beforeImage).toHaveAttribute("src", packageAssetUrl);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/api/assets/input-defaults"))).toBe(false);
  });

  it("uses the source asset behind a masked dashboard image for output comparison", async () => {
    const maskedAssetId = "065c50a0-623b-471f-96a7-075cd7bf25c6.png";
    const sourceAssetId = "2694e2a5-66d8-4998-aca1-48f25503dfe8.png";
    const maskedMetadata = {
      asset_id: maskedAssetId,
      original_filename: "Flux2-Klein_00002_-mask.png",
      content_type: "image/png",
      kind: "image",
      has_mask: true,
      source_asset_id: sourceAssetId,
    };
    const comparisonMetadataResponse = deferred<Response>();
    let maskedMetadataRequestCount = 0;
    let objectUrlIndex = 0;
    let nextObjectUrl: string | null = null;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => {
        if (nextObjectUrl) {
          const url = nextObjectUrl;
          nextObjectUrl = null;
          return url;
        }
        objectUrlIndex += 1;
        return `blob:asset-${objectUrlIndex}`;
      }),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });

    const packageData = {
      ...imageInputPackageData,
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
                id: "ctrl-source-image",
                type: "load_image",
                label: "Source image",
                input_id: "source_image",
                required: true,
                layout: { x: 0, y: 0, w: 12, h: 6 },
              },
              {
                id: "result",
                type: "display_image",
                label: "Result",
                output_id: "image",
                layout: { x: 12, y: 0, w: 12, h: 8 },
              },
            ],
          },
        ],
      },
    };
    const dashboardVersion = dashboardUserStateVersionForTest(packageData);
    let runBody: { inputs?: Record<string, unknown> } | null = null;
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      packageData,
      (init?: RequestInit) => {
        runBody = JSON.parse(String(init?.body ?? "{}"));
        return {
          job_id: "job-masked-edit",
          workflow_id: "text_to_image_v0",
          engine: "comfyui",
          status: "queued",
        };
      },
      (url) => {
        if (url.endsWith(`/api/assets/${maskedAssetId}/metadata`)) {
          maskedMetadataRequestCount += 1;
          return maskedMetadataRequestCount === 1
            ? jsonResponse(maskedMetadata)
            : comparisonMetadataResponse.promise;
        }
        if (url.endsWith(`/api/assets/${sourceAssetId}/metadata`)) {
          return jsonResponse({
            asset_id: sourceAssetId,
            original_filename: "Flux2-Klein_00002_.png",
            content_type: "image/png",
            kind: "image",
          });
        }
        if (url.endsWith(`/api/assets/${maskedAssetId}`)) {
          return imageResponse("masked");
        }
        if (url.endsWith(`/api/assets/${sourceAssetId}`)) {
          nextObjectUrl = "blob:source-asset";
          return imageResponse("source");
        }
        if (url.endsWith("/api/workflows/text_to_image_v0/user-state")) {
          return jsonResponse({
            schema_version: "1",
            workflow_id: "text_to_image_v0",
            dashboard_version: dashboardVersion,
            values: { source_image: maskedAssetId },
            layout_overrides: {},
          });
        }
        if (url.endsWith("/api/jobs/job-masked-edit/progress")) {
          return jsonResponse({
            job_id: "job-masked-edit",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          });
        }
        if (url.endsWith("/api/jobs/job-masked-edit/result")) {
          return jsonResponse({
            job_id: "job-masked-edit",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-masked-edit/outputs/view?filename=edited.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          });
        }
        return undefined;
      },
    );

    const { container } = renderRunPage();

    await waitFor(() => {
      expect(screen.getByAltText("Selected image: Flux2-Klein_00002_-mask.png")).toHaveAttribute("src", "blob:asset-1");
    });
    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));

    await waitFor(() => {
      expect(runBody).toEqual(
        expect.objectContaining({
          inputs: expect.objectContaining({ source_image: maskedAssetId }),
        }),
      );
    });
    expect(screen.queryByRole("slider", { name: /compare original image/i })).not.toBeInTheDocument();

    await act(async () => {
      comparisonMetadataResponse.resolve(jsonResponse(maskedMetadata));
    });

    expect(await screen.findByRole("slider", { name: /compare original image/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(container.querySelector(".image-comparison-slider__image--before")).toHaveAttribute("src", "blob:source-asset");
    });
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith(`/api/assets/${sourceAssetId}`)),
    ).toBe(true);
  });

  it("uses a gallery image reference as the before source for output comparison", async () => {
    const galleryReference = {
      source: "gallery",
      gallery_item_id: "gallery-image-1",
      kind: "image",
      filename: "gallery-source.png",
    };
    const packageData = {
      ...imageInputPackageData,
      inputs: imageInputPackageData.inputs.map((input) => ({
        ...input,
        default: galleryReference,
      })),
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
                id: "ctrl-source-image",
                type: "load_image",
                label: "Source image",
                input_id: "source_image",
                required: true,
                layout: { x: 0, y: 0, w: 12, h: 6 },
              },
              {
                id: "result",
                type: "display_image",
                label: "Result",
                output_id: "image",
                layout: { x: 12, y: 0, w: 12, h: 8 },
              },
            ],
          },
        ],
      },
    };
    mockConfiguredDashboardFetch(
      fetchMock,
      readyRuntime,
      packageData,
      {
        job_id: "job-gallery-edit",
        workflow_id: "text_to_image_v0",
        engine: "comfyui",
        status: "queued",
      },
      (url) => {
        if (url.endsWith("/api/jobs/job-gallery-edit/progress")) {
          return jsonResponse({
            job_id: "job-gallery-edit",
            status: "completed",
            value: 1,
            max: 1,
            current_node: null,
            message: "Execution completed",
          });
        }
        if (url.endsWith("/api/jobs/job-gallery-edit/result")) {
          return jsonResponse({
            job_id: "job-gallery-edit",
            status: "completed",
            outputs: [
              {
                node_id: "9",
                output: {
                  images: [
                    {
                      view_url:
                        "/api/jobs/job-gallery-edit/outputs/view?filename=edited.png&subfolder=&type=output",
                    },
                  ],
                },
              },
            ],
            error: null,
          });
        }
        return undefined;
      },
    );

    const { container } = renderRunPage();

    expect(await screen.findByAltText("Selected image: gallery-source.png")).toHaveAttribute(
      "src",
      "/api/gallery/gallery-image-1/content",
    );
    fireEvent.click(await screen.findByRole("button", { name: /run workflow/i }));

    expect(await screen.findByRole("slider", { name: /compare original image/i })).toBeInTheDocument();
    const beforeImage = container.querySelector(".image-comparison-slider__image--before");
    expect(beforeImage).toHaveAttribute("src", "/api/gallery/gallery-image-1/content");
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
        return Promise.resolve(imageResponse());
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
        return Promise.resolve(imageResponse("input"));
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
