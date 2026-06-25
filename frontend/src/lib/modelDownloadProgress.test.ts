import { describe, expect, it } from "vitest";

import type { ModelDownloadJobStatus } from "./api/models";
import { failedModelMessage, failedModelStatusLabel } from "./modelDownloadProgress";

function failedJob(): ModelDownloadJobStatus {
  return {
    job_id: "job-1",
    status: "completed_with_errors",
    user_facing_message: "Some downloads failed.",
    current_model_filename: null,
    current_model_index: null,
    total_models: 2,
    bytes_downloaded: null,
    total_bytes: null,
    percent: null,
    speed_bytes_per_second: null,
    model_summary: null,
    models: [
      {
        requirement_id: "rate-limited",
        filename: "rate-limited.safetensors",
        status: "rate_limited",
        status_label: "Rate limited",
        bytes_downloaded: null,
        total_bytes: null,
        message: "The provider is rate limiting downloads.",
      },
      {
        requirement_id: "disk-full",
        filename: "disk-full.safetensors",
        status: "not_enough_disk_space",
        status_label: "Not enough disk space",
        bytes_downloaded: null,
        total_bytes: null,
        message: "Not enough free disk space in the configured Noofy Models folder location.",
      },
    ],
  };
}

describe("model download failure summaries", () => {
  it("prioritizes disk-space failures over provider rate limits", () => {
    const job = failedJob();

    expect(failedModelStatusLabel(job)).toBe("Not enough disk space");
    expect(failedModelMessage(job)).toContain("Not enough free disk space");
  });
});
