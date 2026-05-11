import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomePage } from "./HomePage";

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

describe("HomePage", () => {
  const fetchMock = vi.fn();
  const onOpenWorkflow = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
    onOpenWorkflow.mockReset();
    onNavigate.mockReset();
  });

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

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

    expect((await screen.findAllByText("Ready")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("1 workflow loaded locally.")).toBeInTheDocument();
    expect(screen.getAllByText("Installed").length).toBeGreaterThan(0);
    expect(screen.getByText("Noofy Verified")).toBeInTheDocument();
  });

  it("shows starter content and a clear status when the backend is unavailable", async () => {
    fetchMock.mockRejectedValue(new Error("connect failed"));

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

    expect(await screen.findByText("Backend is not reachable")).toBeInTheDocument();
    expect(screen.getAllByText("Offline")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "Text to Image" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Connect backend")).toBeInTheDocument();
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
                label: "Quarantined Community",
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
                label: "Quarantined Community",
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
                label: "Quarantined Community",
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

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

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
    expect(screen.getAllByRole("heading", { name: "EraserV4.5" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Imported")).toBeInTheDocument();
    expect(screen.getAllByText("Quarantined Community").length).toBeGreaterThan(0);
  });

  it("auto-commits a staged import when all required models are already available", async () => {
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
        available_count: 1,
        possible_match_count: 0,
        missing_count: 0,
        needs_manual_download_count: 0,
        ready_to_run: true,
        models: [
          {
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
            status: "available",
            status_label: "Available",
            asset_ownership: "noofy_downloaded",
            source_path: "/models/checkpoints/ready.safetensors",
            matched_root: "/models",
            matched_sha256: "abc",
            matched_size_bytes: 1024,
            message: null,
          },
        ],
      },
    };

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

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

    await screen.findByText("Choose File");
    const file = new File(["archive"], "ready.noofy");
    const fileInput = document.querySelector('input[type="file"][accept=".noofy"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/workflows/import/import-session-ready/commit", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: undefined,
      });
    });
    expect(screen.queryByRole("dialog", { name: "Ready Workflow" })).not.toBeInTheDocument();
    const readyCard = (await screen.findByRole("heading", { name: "Ready Workflow" })).closest("article");
    expect(readyCard?.querySelector(".workflow-status")).toHaveTextContent("Installed");
    expect(readyCard?.querySelector(".workflow-status")).toHaveClass("workflow-status--installed");
  });

  it("commits the staged import after a completed model download job makes models ready", async () => {
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
            import_session_id: "import-session-download",
            user_facing_message: "Imported",
            model_summary: null,
          }),
        );
      }

      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    render(<HomePage onOpenWorkflow={onOpenWorkflow} onNavigate={onNavigate} />);

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
    const workflowCard = (await screen.findByRole("heading", { name: "Core SD15 Text to Image" })).closest("article");
    expect(workflowCard?.querySelector(".workflow-status")).toHaveTextContent("Installed");
    expect(workflowCard?.querySelector(".workflow-status")).toHaveClass("workflow-status--installed");
  });
});
