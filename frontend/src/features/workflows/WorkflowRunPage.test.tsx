import { fireEvent, render, screen } from "@testing-library/react";
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
  });

  it("validates requirements, starts a run, polls progress, and shows the result", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
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

  it("cancels a running job", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(jsonResponse(readyRuntime));
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
});
