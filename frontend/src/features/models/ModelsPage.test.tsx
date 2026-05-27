import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarProvider } from "../app/AppLayout";
import { ModelsPage } from "./ModelsPage";

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const inventory = {
  summary: {
    total_count: 4,
    noofy_count: 1,
    external_comfyui_count: 1,
    missing_count: 1,
    total_known_size_bytes: 4096,
  },
  folders: {
    noofy_models_dir: "/tmp/Noofy Models",
    external_comfyui_models_dir: "/tmp/ComfyUI/models",
    categories: ["checkpoints", "diffusion_models", "loras", "controlnet"],
  },
  tags: [{ id: "tag_sdxl", name: "SDXL", color: "#60a5fa" }],
  models: [
    {
      model_key: "checkpoints/base.safetensors",
      filename: "base.safetensors",
      folder: "checkpoints",
      model_type: "checkpoint",
      size_bytes: 2048,
      status: "ready",
      status_label: "Ready",
      source: "noofy",
      source_label: "Noofy Models",
      ownership: "noofy_imported",
      ownership_label: "Imported into Noofy",
      can_delete: true,
      delete_unavailable_reason: null,
      path: "/tmp/Noofy Models/checkpoints/base.safetensors",
      matched_root: "/tmp/Noofy Models",
      verification_level: "filename_size",
      matched_sha256: null,
      source_availability: null,
      message: null,
      workflow_usage: [],
      downloadable_references: [],
      tag_ids: ["tag_sdxl"],
    },
    {
      model_key: "loras/style.safetensors",
      filename: "style.safetensors",
      folder: "loras",
      model_type: "lora",
      size_bytes: 1024,
      status: "ready",
      status_label: "Ready",
      source: "external_comfyui",
      source_label: "ComfyUI models folder",
      ownership: "external_reference",
      ownership_label: "External reference",
      can_delete: true,
      delete_unavailable_reason: null,
      path: "/tmp/ComfyUI/models/loras/style.safetensors",
      matched_root: "/tmp/ComfyUI/models",
      verification_level: null,
      matched_sha256: null,
      source_availability: null,
      message: null,
      workflow_usage: [],
      downloadable_references: [],
      tag_ids: [],
    },
    {
      model_key: "diffusion_models/flux.safetensors",
      filename: "flux.safetensors",
      folder: "diffusion_models",
      model_type: "diffusion_models",
      size_bytes: 1024,
      status: "ready",
      status_label: "Ready",
      source: "noofy",
      source_label: "Noofy Models",
      ownership: "noofy_local",
      ownership_label: "In Noofy Models",
      can_delete: false,
      delete_unavailable_reason: "Only models imported or downloaded by Noofy can be deleted.",
      path: "/tmp/Noofy Models/diffusion_models/flux.safetensors",
      matched_root: "/tmp/Noofy Models",
      verification_level: null,
      matched_sha256: null,
      source_availability: null,
      message: null,
      workflow_usage: [],
      downloadable_references: [],
      tag_ids: [],
    },
    {
      model_key: "controlnet/missing.safetensors",
      filename: "missing.safetensors",
      folder: "controlnet",
      model_type: "controlnet",
      size_bytes: 1024,
      status: "missing",
      status_label: "Missing",
      source: "required_by_workflow",
      source_label: "Required by workflow",
      ownership: "workflow_requirement",
      ownership_label: "Workflow requirement",
      can_delete: false,
      delete_unavailable_reason: "Missing workflow requirements are not files.",
      path: null,
      matched_root: null,
      verification_level: "filename_size",
      matched_sha256: null,
      source_availability: "known",
      message: null,
      workflow_usage: [
        {
          workflow_id: "wf_text",
          workflow_name: "Text workflow",
          requirement_id: "1:model:controlnet/missing.safetensors",
          status: "missing",
          status_label: "Missing",
        },
      ],
      downloadable_references: [
        {
          workflow_id: "wf_text",
          workflow_name: "Text workflow",
          requirement_id: "1:model:controlnet/missing.safetensors",
        },
      ],
      tag_ids: [],
    },
  ],
};

describe("ModelsPage", () => {
  const fetchMock = vi.fn();
  const onNavigate = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runtime")) {
        return Promise.resolve(
          jsonResponse({
            mode: "managed",
            reachable: true,
            base_url: "http://127.0.0.1:8188",
            repo_dir: "",
            managed_process_running: true,
            sidecar_starting: false,
            pid: 1,
            error: null,
            environment: null,
            crash_count: 0,
            restart_attempt: 0,
            max_restart_attempts: 3,
            uptime_seconds: 1,
            last_crash_at: null,
          }),
        );
      }
      if (url.endsWith("/api/resources")) {
        return Promise.resolve(jsonResponse({ cpu: null, ram: null, vram: null }));
      }
      if (url.endsWith("/api/models")) {
        return Promise.resolve(jsonResponse(inventory));
      }
      if (url.endsWith("/api/models/downloads/active")) {
        return Promise.resolve(jsonResponse({ job: null }));
      }
      if (url.endsWith("/api/models/tags")) {
        return Promise.resolve(jsonResponse({ id: "tag_real", name: "Realistic", color: "#4ade80" }));
      }
      if (url.endsWith("/api/models/downloads")) {
        return Promise.resolve(jsonResponse({ job_id: "job-1", status: "queued", user_facing_message: "Queued" }));
      }
      if (url.endsWith("/api/models/downloads/job-1")) {
        return Promise.resolve(
          jsonResponse({
            job_id: "job-1",
            status: "completed",
            user_facing_message: "Model download check finished.",
            current_model_filename: "missing.safetensors",
            current_model_index: 1,
            total_models: 1,
            bytes_downloaded: 1024,
            total_bytes: 1024,
            percent: 100,
            speed_bytes_per_second: null,
            models: [],
          }),
        );
      }
      if (url.endsWith("/api/models/checkpoints%2Fbase.safetensors") && init?.method === "DELETE") {
        return Promise.resolve(jsonResponse({ model_key: "checkpoints/base.safetensors", deleted: true, message: "Deleted" }));
      }
      if (url.endsWith("/api/models/loras%2Fstyle.safetensors") && init?.method === "DELETE") {
        return Promise.resolve(jsonResponse({ model_key: "loras/style.safetensors", deleted: true, message: "Deleted" }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function renderPage() {
    return render(
      <SidebarProvider>
        <ModelsPage onNavigate={onNavigate} />
      </SidebarProvider>,
    );
  }

  it("renders real model inventory, source labels, and filters rows", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByText("base.safetensors")).toBeInTheDocument();
    expect(screen.getAllByText("ComfyUI models folder").length).toBeGreaterThan(0);
    expect(screen.getByText("Required by Text workflow")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search models..."), { target: { value: "style" } });

    expect(screen.queryByText("base.safetensors")).not.toBeInTheDocument();
    expect(screen.getByText("style.safetensors")).toBeInTheDocument();
  });

  it("uses workflow names for missing workflow requirement source labels", async () => {
    renderPage();

    fireEvent.click(await screen.findByText("missing.safetensors"));

    expect(screen.getAllByText("Required by Text workflow").length).toBeGreaterThan(0);
  });

  it("groups diffusion models with base models", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Base models" }));

    expect(screen.getByText("base.safetensors")).toBeInTheDocument();
    expect(screen.getByText("flux.safetensors")).toBeInTheDocument();
    expect(screen.queryByText("style.safetensors")).not.toBeInTheDocument();
    expect(screen.getByText("Base model · checkpoints")).toBeInTheDocument();
    expect(screen.getByText("Base model · diffusion models")).toBeInTheDocument();
  });

  it("starts a backend-owned download for missing models", async () => {
    renderPage();

    const downloadButton = await screen.findByRole("button", { name: "Download missing" });
    fireEvent.click(downloadButton);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/models/downloads",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            selections: [{ workflow_id: "wf_text", requirement_id: "1:model:controlnet/missing.safetensors" }],
          }),
        }),
      );
    });
  });

  it("deletes selected Noofy-managed model files", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage();

    fireEvent.click(await screen.findByLabelText("Select base.safetensors"));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/models/checkpoints%2Fbase.safetensors",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(screen.getByText("Deleted 1 model from Noofy Models.")).toBeInTheDocument();
  });

  it("skips selected models that are not deletable", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage();

    fireEvent.click(await screen.findByLabelText("Select all models"));
    expect(screen.getByText("4 selected, 2 can be deleted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/models/checkpoints%2Fbase.safetensors",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/models/loras%2Fstyle.safetensors",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/models/controlnet%2Fmissing.safetensors",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("shows delete for Noofy-owned files and external ComfyUI model files", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    renderPage();

    fireEvent.click(await screen.findByText("base.safetensors"));
    expect(screen.getByRole("button", { name: "Delete from Noofy Models" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete from Noofy Models" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/models/checkpoints%2Fbase.safetensors",
        expect.objectContaining({ method: "DELETE" }),
      );
    });

    fireEvent.click(screen.getByText("style.safetensors"));
    expect(screen.getByRole("button", { name: "Delete from ComfyUI folder" })).toBeInTheDocument();
  });
});
