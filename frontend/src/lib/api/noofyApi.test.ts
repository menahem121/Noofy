import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createJobEventsUrl,
  fetchRuntimeStatus,
  fetchTrustPolicy,
  fetchWorkflows,
  importWorkflowPackage,
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

  it("uses runtime API base URL and token for job event streams", () => {
    window.__NOOFY_RUNTIME_CONFIG__ = {
      apiBaseUrl: "http://127.0.0.1:9123/api/",
      apiToken: "runtime secret",
    };

    expect(createJobEventsUrl("job 1")).toBe(
      "http://127.0.0.1:9123/api/jobs/job%201/events?token=runtime%20secret",
    );
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
});
