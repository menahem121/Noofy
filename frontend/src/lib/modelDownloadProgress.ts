import type { ImportModelDownloadJobStatus, ModelDownloadJobStatus } from "./api/noofyApi";

type DownloadJob = ImportModelDownloadJobStatus | ModelDownloadJobStatus;

const failedStatuses = new Set([
  "failed",
  "completed_with_errors",
  "download_failed",
  "verification_failed",
  "authentication_required",
  "access_denied",
  "rate_limited",
  "hash_mismatch",
  "not_enough_disk_space",
]);

export function isModelDownloadActive(status: string | null | undefined) {
  return status === "queued" || status === "pending" || status === "running" || status === "downloading" || status === "verifying";
}

export function isModelDownloadFailure(status: string | null | undefined) {
  return Boolean(status && failedStatuses.has(status));
}

export function modelDownloadPanelTone(job: DownloadJob) {
  if (isModelDownloadFailure(job.status)) return "failed";
  if (job.status === "canceled") return "canceled";
  if (job.status === "completed" || job.status === "succeeded") return "succeeded";
  if (job.models.some((model) => model.status === "verifying")) return "verifying";
  return "active";
}

export function modelDownloadPercentLabel(job: DownloadJob, percent: number | null) {
  if (job.status === "completed_with_errors") return "Some failed";
  if (job.status === "failed") return failedModelStatusLabel(job) ?? "Failed";
  if (job.status === "canceled") return "Canceled";
  if (job.status === "succeeded") return "Downloaded";
  if (job.models.some((model) => model.status === "verifying")) return "Verifying";
  if (percent !== null && percent >= 100 && job.status !== "completed") return "Finishing";
  if (percent !== null) return `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`;
  return job.status;
}

export function failedModelStatusLabel(job: DownloadJob) {
  const failedModel = job.models.find((model) => isModelDownloadFailure(model.status));
  return failedModel?.status_label ?? failedModelStatusFromStatus(failedModel?.status);
}

export function failedModelMessage(job: DownloadJob) {
  const failedModel = job.models.find((model) => isModelDownloadFailure(model.status));
  return failedModel?.message ?? null;
}

function failedModelStatusFromStatus(status: string | null | undefined) {
  switch (status) {
    case "verification_failed":
    case "hash_mismatch":
      return "Verification failed";
    case "authentication_required":
      return "Authentication required";
    case "access_denied":
      return "Access denied";
    case "rate_limited":
      return "Rate limited";
    case "not_enough_disk_space":
      return "Not enough disk space";
    case "download_failed":
      return "Download failed";
    default:
      return null;
  }
}
