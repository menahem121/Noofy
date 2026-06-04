import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RequiredModelAvailability, WorkflowImportResponse } from "../../lib/api/noofyApi";
import { RequiredModelsModal } from "./WorkflowImportModals";

const missingModel = {
  requirement_id: "vae/flux2-vae.safetensors",
  node_id: "1",
  node_type: "VAELoader",
  input_name: "vae_name",
  filename: "flux2-vae.safetensors",
  model_type: "vae",
  folder: "vae",
  verification_level: "sha256_size",
  size_bytes: 336213556,
  source_urls: ["https://huggingface.co/example/flux2-vae.safetensors"],
  source_availability: "known",
  status: "missing",
  status_label: "Missing",
  asset_ownership: "external_reference",
  source_path: null,
  matched_root: null,
  matched_sha256: null,
  matched_size_bytes: null,
  message: "Noofy can try to resolve and download this model before the workflow runs.",
  references: [
    { requirement_id: "vae/flux2-vae.safetensors", node_id: "1", node_type: "VAELoader", input_name: "vae_name" },
  ],
  reference_count: 1,
  dedup_uncertain: false,
} satisfies RequiredModelAvailability;

const importResult = {
  import_session_id: "import-session-1",
  workflow_id: "flux-workflow",
  status: "imported",
  user_facing_message: "Ready to import",
  workflow: {
    id: "flux-workflow",
    name: "Flux Workflow",
    version: "0.1.0",
    description: "",
    trust_level: "noofy_verified",
  },
  required_model_count: 1,
  custom_node_count: 0,
  unresolved_input_count: 0,
  model_summary: {
    workflow_id: "flux-workflow",
    total_count: 1,
    available_count: 0,
    possible_match_count: 0,
    missing_count: 1,
    needs_manual_download_count: 0,
    ready_to_run: false,
    models: [missingModel],
  },
  duplicate_identity: null,
} satisfies WorkflowImportResponse;

describe("RequiredModelsModal", () => {
  it("renders verification failures as failed instead of successful 100 percent downloads", () => {
    const onDownload = vi.fn();
    render(
      <RequiredModelsModal
        importResult={importResult}
        busy={false}
        importing={false}
        downloadJob={{
          job_id: "download-1",
          import_session_id: "import-session-1",
          workflow_id: "flux-workflow",
          status: "failed",
          user_facing_message: "Some downloads failed.",
          current_model_filename: "flux2-vae.safetensors",
          current_model_index: 1,
          total_models: 1,
          bytes_downloaded: 336213556,
          total_bytes: 336213556,
          percent: 100,
          speed_bytes_per_second: null,
          models: [
            {
              requirement_id: "vae/flux2-vae.safetensors",
              filename: "flux2-vae.safetensors",
              status: "verification_failed",
              status_label: "Verification failed",
              bytes_downloaded: 336213556,
              total_bytes: 336213556,
              message: "The downloaded model did not match the expected file size.",
            },
          ],
          model_summary: {
            ...importResult.model_summary,
            models: [
              {
                ...missingModel,
                status: "verification_failed",
                status_label: "Verification failed",
                message: "The downloaded model did not match the expected file size.",
              },
            ],
          },
        }}
        verificationJob={null}
        onDownload={onDownload}
        onCancelDownload={vi.fn()}
        onContinue={vi.fn()}
        onReplace={vi.fn()}
        onCopy={vi.fn()}
        onReadyAction={vi.fn()}
        onCancel={vi.fn()}
        onViewModels={vi.fn()}
      />,
    );

    expect(screen.getAllByText("Verification failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("The downloaded model did not match the expected file size.").length).toBeGreaterThan(0);
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
    expect(document.querySelector(".model-download-progress")).toHaveClass("model-download-progress--failed");

    fireEvent.click(screen.getByRole("button", { name: "Retry Download" }));
    expect(onDownload).toHaveBeenCalledTimes(1);
  });

  it("replaces retry with View Models for disk-space download failures", () => {
    const onDownload = vi.fn();
    const onViewModels = vi.fn();
    render(
      <RequiredModelsModal
        importResult={importResult}
        busy={false}
        importing={false}
        downloadJob={{
          job_id: "download-1",
          import_session_id: "import-session-1",
          workflow_id: "flux-workflow",
          status: "completed_with_errors",
          user_facing_message: "Some downloads failed.",
          current_model_filename: "flux2-vae.safetensors",
          current_model_index: 1,
          total_models: 1,
          bytes_downloaded: 0,
          total_bytes: 336213556,
          percent: null,
          speed_bytes_per_second: null,
          models: [
            {
              requirement_id: "vae/flux2-vae.safetensors",
              filename: "flux2-vae.safetensors",
              status: "not_enough_disk_space",
              status_label: "Not enough disk space",
              bytes_downloaded: 0,
              total_bytes: 336213556,
              message: "Not enough free disk space in the configured Noofy Models folder location.",
            },
          ],
          model_summary: {
            ...importResult.model_summary,
            models: [
              {
                ...missingModel,
                status: "not_enough_disk_space",
                status_label: "Not enough disk space",
                message: "Not enough free disk space in the configured Noofy Models folder location.",
              },
            ],
          },
        }}
        verificationJob={null}
        onDownload={onDownload}
        onCancelDownload={vi.fn()}
        onContinue={vi.fn()}
        onReplace={vi.fn()}
        onCopy={vi.fn()}
        onReadyAction={vi.fn()}
        onCancel={vi.fn()}
        onViewModels={onViewModels}
      />,
    );

    expect(screen.queryByRole("button", { name: "Retry Download" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View Models" }));
    expect(onViewModels).toHaveBeenCalledTimes(1);
    expect(onDownload).not.toHaveBeenCalled();
  });

  it("shows one card with a node-usage summary when a file is loaded by several nodes", () => {
    const sharedModel = {
      ...missingModel,
      requirement_id: "221:ckpt_name:checkpoints/ltx.safetensors",
      filename: "ltx-2.3-22b-dev-fp8.safetensors",
      folder: "checkpoints",
      model_type: "checkpoint",
      reference_count: 3,
      references: [
        { requirement_id: "221:ckpt_name:checkpoints/ltx.safetensors", node_id: "221", node_type: "LTXVAudioVAELoader", input_name: "ckpt_name" },
        { requirement_id: "236:ckpt_name:checkpoints/ltx.safetensors", node_id: "236", node_type: "CheckpointLoaderSimple", input_name: "ckpt_name" },
        { requirement_id: "243:ckpt_name:checkpoints/ltx.safetensors", node_id: "243", node_type: "LTXAVTextEncoderLoader", input_name: "ckpt_name" },
      ],
    } satisfies RequiredModelAvailability;
    const result = {
      ...importResult,
      model_summary: { ...importResult.model_summary, models: [sharedModel] },
    } satisfies WorkflowImportResponse;

    render(
      <RequiredModelsModal
        importResult={result}
        busy={false}
        importing={false}
        downloadJob={null}
        verificationJob={null}
        onDownload={vi.fn()}
        onCancelDownload={vi.fn()}
        onContinue={vi.fn()}
        onReplace={vi.fn()}
        onCopy={vi.fn()}
        onReadyAction={vi.fn()}
        onCancel={vi.fn()}
        onViewModels={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("heading", { name: "ltx-2.3-22b-dev-fp8.safetensors" })).toHaveLength(1);
    expect(screen.getByText("Used in 3 places in this workflow")).toBeInTheDocument();
    expect(screen.getByText("Show technical details")).toBeInTheDocument();
    expect(screen.getByText("Workflow nodes (3)")).toBeInTheDocument();
    expect(screen.getByText(/CheckpointLoaderSimple/)).toBeInTheDocument();
  });
});
