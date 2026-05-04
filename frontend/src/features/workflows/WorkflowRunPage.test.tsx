import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function renderRunPage() {
  return render(<WorkflowRunPage workflowId="text_to_image_v0" onBack={vi.fn()} onNavigate={vi.fn()} />);
}

describe("WorkflowRunPage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
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
            outputs: [{ output: { images: [{ view_url: "/api/view?filename=result.png" }] } }],
            error: null,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    renderRunPage();

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    expect(await screen.findByText("Result saved by the local workflow.")).toBeInTheDocument();
    expect(screen.getByAltText("Generated workflow output")).toHaveAttribute("src", "/api/view?filename=result.png");
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

    expect(await screen.findByText("This workflow needs one missing model")).toBeInTheDocument();
    expect(screen.getByText("v1-5-pruned-emaonly-fp16.safetensors")).toBeInTheDocument();
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));
    expect(await screen.findByText("running")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(await screen.findByText("Run canceled.")).toBeInTheDocument();
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

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run workflow/i }));

    await waitFor(() => {
      expect(eventSourceMock).toHaveBeenCalledWith("/api/jobs/job-4/events?token=runtime-secret");
    });
  });
});
