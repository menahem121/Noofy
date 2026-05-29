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
      />,
    );

    expect(screen.getAllByText("Verification failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("The downloaded model did not match the expected file size.").length).toBeGreaterThan(0);
    expect(screen.queryByText("100%")).not.toBeInTheDocument();
    expect(document.querySelector(".model-download-progress")).toHaveClass("model-download-progress--failed");

    fireEvent.click(screen.getByRole("button", { name: "Retry Download" }));
    expect(onDownload).toHaveBeenCalledTimes(1);
  });
});
