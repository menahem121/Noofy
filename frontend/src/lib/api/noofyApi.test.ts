import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelModelDownload,
  createModelTag,
  createJobEventsUrl,
  deleteModelFile,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchActiveModelDownload,
  fetchModelDownloadStatus,
  fetchModelInventory,
  fetchComfyUILaunchSettings,
  fetchComfyUIUpdateStatus,
  fetchComfyUIVersions,
  fetchResourceSnapshot,
  fetchRuntimeStatus,
  fetchTrustPolicy,
  fetchWorkflows,
  importWorkflowPackage,
  importModelFiles,
  rebuildComfyUI,
  resolveBackendUrl,
  startModelDownload,
  updateComfyUI,
  updateComfyUILaunchSettings,
  updateModelTags,
} from "./noofyApi";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

describe("noofyApi", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    delete window.__NOOFY_RUNTIME_CONFIG__;
  });

  it("works without a token for browser development", async () => {
    fetchMock.mockResolvedValue(jsonResponse([]));

    await fetchWorkflows();

    expect(fetchMock).toHaveBeenCalledWith("/api/workflows", {
      headers: {
        Accept: "application/json",
      },
    });
  });

  it("sends Authorization when a runtime token is configured", async () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiToken: "runtime-secret",
    };
    fetchMock.mockResolvedValue(
      jsonResponse({
        mode: "managed",
        reachable: true,
        base_url: "http://127.0.0.1:8188",
        repo_dir: "/tmp/ComfyUI",
        managed_process_running: true,
        pid: 123,
        error: null,
        environment: null,
      }),
    );

    await fetchRuntimeStatus();

    expect(fetchMock).toHaveBeenCalledWith("/api/runtime", {
      headers: {
        Accept: "application/json",
        Authorization: "Bearer runtime-secret",
      },
    });
  });

  it("uses runtime API base URL for backend requests", async () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiBaseUrl: "http://127.0.0.1:9123/api/",
    };
    fetchMock.mockResolvedValue(jsonResponse({}));

    await fetchRuntimeStatus();

    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:9123/api/runtime", {
      headers: {
        Accept: "application/json",
      },
    });
  });

  it("fetches resource snapshots through the Noofy backend API", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        observed_at: "2026-05-08T10:00:00+00:00",
        cpu: { available: true, percent: 23, used_mb: null, total_mb: null, free_mb: null, source: "test", error: null },
        ram: { available: true, percent: 35, used_mb: 11264, total_mb: 32768, free_mb: 21504, source: "test", error: null },
        vram: { available: false, percent: null, used_mb: null, total_mb: null, free_mb: null, source: null, error: "vram_unavailable" },
        backend: "cpu",
        device_name: null,
        memory_pressure: "low",
      }),
    );

    const snapshot = await fetchResourceSnapshot();

    expect(snapshot.cpu.percent).toBe(23);
    expect(fetchMock).toHaveBeenCalledWith("/api/resources", {
      headers: { Accept: "application/json" },
    });
  });

  it("uses runtime API base URL and token for job event streams", () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiBaseUrl: "http://127.0.0.1:9123/api/",
      apiToken: "runtime secret",
    };

    expect(createJobEventsUrl("job 1")).toBe(
      "http://127.0.0.1:9123/api/jobs/job%201/events?token=runtime%20secret",
    );
  });

  it("resolves backend-provided media URLs through the active API base URL", () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiBaseUrl: "http://127.0.0.1:9123/api/",
      apiToken: "runtime secret",
    };

    expect(
      resolveBackendUrl("/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output", {
        includeToken: true,
      }),
    ).toBe(
      "http://127.0.0.1:9123/api/jobs/job-1/outputs/view?filename=result.png&subfolder=&type=output&token=runtime%20secret",
    );
    expect(resolveBackendUrl("https://example.test/image.png", { includeToken: true })).toBe(
      "https://example.test/image.png",
    );
  });

  it("adds the runtime token to browser download workflow export URLs", () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiBaseUrl: "http://127.0.0.1:9123/api/",
      apiToken: "runtime secret",
    };

    expect(exportWorkflowUrl("workflow 1")).toBe(
      "http://127.0.0.1:9123/api/workflows/workflow%201/export?token=runtime%20secret",
    );
    expect(exportWorkflowComfyJsonUrl("workflow 1")).toBe(
      "http://127.0.0.1:9123/api/workflows/workflow%201/export/comfyui-json?token=runtime%20secret",
    );
  });

  it("uses backend endpoints for ComfyUI version updates and launch settings", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ status: "running" })));

    await fetchComfyUIVersions();
    await fetchComfyUILaunchSettings();
    await updateComfyUI("v0.20.1");
    await rebuildComfyUI("v0.20.1");
    await fetchComfyUIUpdateStatus();
    await updateComfyUILaunchSettings("lowvram");
    await fetchComfyUIVersions({ checkUpstream: true });

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/engine/comfyui/versions", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/engine/comfyui/launch-settings", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/engine/comfyui/update", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ version: "v0.20.1" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/engine/comfyui/rebuild", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ version: "v0.20.1" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/engine/comfyui/update/status", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(6, "/api/engine/comfyui/launch-settings", {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ vram_mode: "lowvram" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(7, "/api/engine/comfyui/versions?check_upstream=true", {
      headers: { Accept: "application/json" },
    });
  });

  it("uploads workflow packages through the Noofy backend API", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        workflow_id: "unknown__eraserv4.5__0.1.0",
        status: "needs_input_setup",
        user_facing_message: "Needs input setup",
        workflow: {
          id: "unknown__eraserv4.5__0.1.0",
          name: "EraserV4.5",
          version: "0.1.0",
          description: "",
        },
        required_model_count: 2,
        custom_node_count: 5,
        unresolved_input_count: 1,
      }),
    );

    const result = await importWorkflowPackage(new File(["archive"], "eraser workflow.noofy"));

    expect(result.status).toBe("needs_input_setup");
    expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import?filename=eraser%20workflow.noofy", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/octet-stream",
      },
      body: expect.any(ArrayBuffer),
    });
  });

  it("fetches public trust policy metadata from the Noofy backend API", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        schema_version: "0.1.0",
        signature_payload_schema_version: "0.1.0",
        trusted_key_count: 0,
        trusted_keys: [],
        trust_levels: {},
        imported_trusted_claims_require_verified_evidence: true,
        secrets_exposed: false,
      }),
    );

    const result = await fetchTrustPolicy();

    expect(result.secrets_exposed).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith("/api/trust/policy", {
      headers: {
        Accept: "application/json",
      },
    });
  });

  it("sends explicit community preparation opt-in only when selected", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        workflow_id: "unknown__community__0.1.0",
        status: "blocked_by_policy",
        user_facing_message: "Needs permission to prepare community workflow",
        workflow: {
          id: "unknown__community__0.1.0",
          name: "Community",
          version: "0.1.0",
          description: "",
        },
        required_model_count: 0,
        custom_node_count: 1,
        unresolved_input_count: 0,
      }),
    );

    await importWorkflowPackage(new File(["archive"], "community.noofy"), true);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/workflows/import?filename=community.noofy&allow_unverified_community_preparation=true",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("uses backend-owned model inventory, tag, import, and download endpoints", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ ok: true, models: [], tags: [], summary: {}, folders: {} })));

    await fetchModelInventory();
    await importModelFiles({ source_paths: ["/tmp/model.safetensors"], folder: "checkpoints" });
    await createModelTag({ name: "SDXL", color: "#60a5fa" });
    await updateModelTags("checkpoints/model.safetensors", ["tag_1"]);
    await startModelDownload([{ workflow_id: "wf", requirement_id: "1:model:checkpoints/model.safetensors" }]);
    await fetchActiveModelDownload();
    await fetchModelDownloadStatus("job-1");
    await cancelModelDownload("job-1");
    await deleteModelFile("checkpoints/model.safetensors");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/models", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/models/import", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ source_paths: ["/tmp/model.safetensors"], folder: "checkpoints" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/models/tags", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: "SDXL", color: "#60a5fa" }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(4, "/api/models/checkpoints%2Fmodel.safetensors/tags", {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tag_ids: ["tag_1"] }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/models/downloads", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        selections: [{ workflow_id: "wf", requirement_id: "1:model:checkpoints/model.safetensors" }],
      }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(6, "/api/models/downloads/active", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(7, "/api/models/downloads/job-1", {
      headers: { Accept: "application/json" },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(8, "/api/models/downloads/job-1/cancel", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
    });
    expect(fetchMock).toHaveBeenNthCalledWith(9, "/api/models/checkpoints%2Fmodel.safetensors", {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
  });

  it("surfaces structured backend error messages", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: { code: "model_import_error", message: "A model already exists." } }, 400));

    await expect(importModelFiles({ source_paths: ["/tmp/model.safetensors"], folder: "checkpoints" })).rejects.toThrow(
      "A model already exists.",
    );
  });
});
