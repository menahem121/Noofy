import {
  AlertCircle,
  CheckCircle2,
  Download,
  Loader2,
  X,
} from "lucide-react";

import {
  type ModelDownloadJobStatus,
  type ModelDownloadSelection,
  type RequiredModelAvailability,
  type RequiredModelSummary,
  type WorkflowModelVerificationJobStatus,
  type WorkflowPackageResponse,
  type WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import {
  failedModelMessage,
  isModelDownloadActive,
  isModelDownloadFailure,
  modelDownloadPanelTone,
  modelDownloadPercentLabel,
} from "../../lib/modelDownloadProgress";
import { ModelReferenceDetails } from "./ModelReferenceDetails";
import { ModelVerificationProgressPanel } from "./ModelVerificationProgressPanel";
import { requiredModelTypeLabel } from "./requiredModelLabels";

export function activeRequiredModelSummary(
  summary: RequiredModelSummary | null,
  packageData: WorkflowPackageResponse | null,
  inputValues: Record<string, unknown>,
): RequiredModelSummary | null {
  if (!summary) return null;
  const bypassedModelKeys = bypassedLoraModelKeys(packageData, inputValues);
  if (bypassedModelKeys.size === 0) return summary;
  const models = summary.models.filter((model) => !bypassedModelKeys.has(requiredModelKey(model)));
  const availableCount = models.filter((model) => model.status === "available").length;
  return {
    ...summary,
    models,
    total_count: models.length,
    available_count: availableCount,
    possible_match_count: models.filter((model) => model.status === "possible_match").length,
    missing_count: models.filter((model) => model.status === "missing").length,
    needs_manual_download_count: models.filter((model) => model.status === "needs_manual_download").length,
    ready_to_run: models.length === availableCount,
  };
}

export function normalizedLoraInputValues(
  packageData: WorkflowPackageResponse | null,
  inputValues: Record<string, unknown>,
): Record<string, unknown> {
  if (!packageData) return inputValues as Record<string, unknown>;
  let normalized: Record<string, unknown> | null = null;
  for (const input of packageData.inputs) {
    if (input.control !== "lora_loader") continue;
    if (!isEmptyWorkflowValue(inputValues[input.id])) continue;
    normalized ??= { ...inputValues };
    normalized[input.id] = "None";
  }
  return normalized ?? (inputValues as Record<string, unknown>);
}

export function activeWorkflowValidation(
  validation: WorkflowValidationResult | null,
  packageData: WorkflowPackageResponse | null,
  inputValues: Record<string, unknown>,
): WorkflowValidationResult | null {
  if (!validation) return null;
  const bypassedModelKeys = bypassedLoraModelKeys(packageData, inputValues);
  if (bypassedModelKeys.size === 0) return validation;
  const missingModels = validation.missing_models.filter((model) => !bypassedModelKeys.has(requiredModelKey(model)));
  return {
    ...validation,
    missing_models: missingModels,
    valid: validation.errors.length === 0 && missingModels.length === 0,
  };
}

export function requiredModelDownloadSelections(
  summary: RequiredModelSummary | null,
  workflowId: string,
): ModelDownloadSelection[] {
  if (!summary) return [];
  return summary.models
    .filter(isRequiredModelDownloadRetryable)
    .map((model) => ({ workflow_id: workflowId, requirement_id: model.requirement_id }));
}

export function hasVerifiableLocalModels(summary: RequiredModelSummary | null) {
  return Boolean(summary?.models.some((model) => model.status === "possible_match"));
}

export function isRequiredModelVerificationPending(model: RequiredModelAvailability) {
  return model.status === "checking" || model.status === "possible_match";
}

export function isRequiredModelNonReady(model: RequiredModelAvailability) {
  return model.status !== "available" && !isRequiredModelVerificationPending(model);
}

export function requiredModelSummaryHasPendingVerification(summary: RequiredModelSummary | null) {
  return Boolean(summary?.models.some(isRequiredModelVerificationPending));
}

export function requiredModelSummaryHasNonReadyModels(summary: RequiredModelSummary | null) {
  return Boolean(summary?.models.some(isRequiredModelNonReady));
}

export function WorkflowRequiredModelsModal({
  workflowName,
  summary,
  downloadJob,
  downloadError,
  downloadBusy,
  verificationJob,
  verificationError,
  onDownload,
  onCancelDownload,
  onRetryVerification,
  onClose,
}: {
  workflowName: string;
  summary: RequiredModelSummary;
  downloadJob: ModelDownloadJobStatus | null;
  downloadError: string | null;
  downloadBusy: boolean;
  verificationJob: WorkflowModelVerificationJobStatus | null;
  verificationError: string | null;
  onDownload: () => void;
  onCancelDownload: () => void;
  onRetryVerification: () => void;
  onClose: () => void;
}) {
  const effectiveSummary = verificationJob?.model_summary ?? summary;
  const activeDownload = Boolean(downloadJob && isModelDownloadActive(downloadJob.status));
  const activeVerification = Boolean(verificationJob && ["queued", "running"].includes(verificationJob.status));
  const downloadable = effectiveSummary.models.some(isRequiredModelDownloadRetryable);
  const progressByRequirement = new Map(downloadJob?.models.map((model) => [model.requirement_id, model]) ?? []);
  const readyToRun = effectiveSummary.ready_to_run;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-required-models-title">
      <section className="required-models-modal">
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Required models</p>
            <h2 id="workflow-required-models-title">Missing Models</h2>
            <p>
              {readyToRun
                ? `${workflowName} has all required model files available.`
                : `${workflowName} needs required model files before it can run.`}
            </p>
          </div>
          <button className="icon-button" type="button" aria-label="Close missing models" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-modal__body">
          <div className="required-models-list">
            {effectiveSummary.models.map((model) => (
              <WorkflowRequiredModelRow
                key={model.requirement_id}
                model={model}
                progress={progressByRequirement.get(model.requirement_id)}
              />
            ))}
          </div>

          {activeVerification ? (
            <ModelVerificationProgressPanel
              job={verificationJob}
              idleLabel="Verifying local model..."
              idleMessage="Verifying local model..."
            />
          ) : null}
          {verificationJob?.status === "completed" && readyToRun ? (
            <div className="notice notice--success notice--compact" role="status">
              <CheckCircle2 size={16} aria-hidden="true" />
              <div>
                <strong>Local model verified</strong>
                <span>Noofy can use the matching local file for this workflow.</span>
              </div>
            </div>
          ) : null}
          {verificationJob?.status === "completed" && !readyToRun && hasVerifiableLocalModels(effectiveSummary) ? (
            <div className="notice notice--warning notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Local model could not be accepted</strong>
                <span>Noofy checked the matching local file, but it did not pass the required verification.</span>
              </div>
            </div>
          ) : null}
          {(verificationError || verificationJob?.status === "failed") ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Model verification failed</strong>
                <span>{verificationError ?? verificationJob?.user_facing_message ?? "Noofy could not verify the local model file."}</span>
              </div>
              <button className="secondary-button secondary-button--small" type="button" onClick={onRetryVerification}>
                Verify Again
              </button>
            </div>
          ) : null}
          {downloadJob && shouldShowModelDownloadProgress(downloadJob) ? <WorkflowModelDownloadProgress job={downloadJob} onRetry={onDownload} /> : null}
          {downloadError ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Model download failed</strong>
                <span>{downloadError}</span>
              </div>
            </div>
          ) : null}
        </div>

        <footer className="required-models-modal__footer">
          <button className="secondary-button" type="button" disabled={downloadBusy || activeDownload || activeVerification || !downloadable} onClick={onDownload}>
            {downloadBusy || activeDownload ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
            {downloadBusy || activeDownload ? "Downloading..." : "Download Missing Models"}
          </button>
          {activeDownload ? (
            <button className="secondary-button" type="button" onClick={onCancelDownload}>
              Cancel Download
            </button>
          ) : null}
          <button className="ghost-button" type="button" onClick={onClose}>
            Close
          </button>
        </footer>
      </section>
    </div>
  );
}

function WorkflowRequiredModelRow({
  model,
  progress,
}: {
  model: RequiredModelAvailability;
  progress?: ModelDownloadJobStatus["models"][number];
}) {
  const status = progress?.status ?? model.status;
  const statusLabel = progress?.status_label ?? model.status_label;
  const message = progress?.message ?? model.message;
  return (
    <article className="required-model-row">
      <div className="required-model-row__main">
        <h3>{model.filename}</h3>
        <p>
          {[requiredModelTypeLabel(model.folder, model.model_type), formatRequiredModelSize(model.size_bytes)]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {model.reference_count > 1 ? (
          <span className="required-model-row__usage">
            Used in {model.reference_count} places in this workflow
          </span>
        ) : null}
        {message ? <span className="required-model-row__message">{message}</span> : null}
        <ModelReferenceDetails references={model.references} dedupUncertain={model.dedup_uncertain} />
      </div>
      <div className="required-model-row__meta">
        <span className="model-identity">{requiredModelVerificationLabel(model.verification_level)}</span>
        <span className={`model-status-pill model-status-pill--${status}`}>{statusLabel}</span>
        <span className="model-source">{requiredModelSourceLabel(model)}</span>
      </div>
    </article>
  );
}

function WorkflowModelDownloadProgress({ job, onRetry }: { job: ModelDownloadJobStatus; onRetry: () => void }) {
  const label = job.current_model_filename
    ? `Model ${job.current_model_index ?? 1} of ${job.total_models}: ${job.current_model_filename}`
    : job.user_facing_message;
  const rawPercent = job.percent ?? (
    job.bytes_downloaded !== null && job.total_bytes
      ? Math.round((job.bytes_downloaded / job.total_bytes) * 100)
      : null
  );
  const percent = rawPercent !== null && Number.isFinite(Number(rawPercent))
    ? Math.max(0, Math.min(Number(rawPercent), 100))
    : null;
  const percentLabel = modelDownloadPercentLabel(job, percent);
  const tone = modelDownloadPanelTone(job);
  const failureMessage = failedModelMessage(job);

  return (
    <div className={`model-download-progress model-download-progress--${tone}`} role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span className="model-download-progress__status">{percentLabel}</span>
      </div>
      {percent !== null ? (
        <div
          className="model-download-progress__bar"
          role="progressbar"
          aria-label="Model download progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div className="model-download-progress__bar-fill" style={{ width: `${percent}%` }} />
        </div>
      ) : null}
      <p>
        {[formatRequiredModelSize(job.bytes_downloaded), job.total_bytes ? formatRequiredModelSize(job.total_bytes) : null]
          .filter(Boolean)
          .join(" / ")}
        {job.speed_bytes_per_second ? ` · ${formatRequiredModelSpeed(job.speed_bytes_per_second)}` : ""}
      </p>
      <span>{job.user_facing_message}</span>
      {failureMessage ? <span className="model-download-progress__failure">{failureMessage}</span> : null}
      {isModelDownloadFailure(job.status) ? (
        <button className="secondary-button secondary-button--small" type="button" onClick={onRetry}>
          Retry Download
        </button>
      ) : null}
    </div>
  );
}

function shouldShowModelDownloadProgress(job: ModelDownloadJobStatus) {
  if (
    [
      "pending",
      "queued",
      "running",
      "downloading",
      "verifying",
      "succeeded",
      "completed",
      "completed_with_errors",
      "failed",
      "canceled",
    ].includes(job.status)
  ) return true;
  return job.percent !== null || job.bytes_downloaded !== null;
}

function requiredModelVerificationLabel(level: string) {
  if (level === "sha256_size") return "Verified file";
  if (level === "filename_size") return "Name and size match";
  if (level === "filename_only") return "Name match";
  return "Model check";
}

function requiredModelSourceLabel(model: RequiredModelAvailability) {
  if (model.source_urls.length > 0) return "Download source known";
  if (model.source_availability === "resolvable") return "Can search known sources";
  return "No download source";
}

function isRequiredModelDownloadRetryable(model: RequiredModelAvailability) {
  return (
    retryableRequiredModelStatuses.has(model.status) ||
    (model.status === "possible_match" && model.source_availability === "resolvable")
  );
}

function formatRequiredModelSize(size: number | null) {
  if (!size) return null;
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)} GB`;
  if (size >= 1024 ** 2) return `${Math.round(size / 1024 ** 2)} MB`;
  return `${Math.round(size / 1024)} KB`;
}

function formatRequiredModelSpeed(bytesPerSecond: number) {
  const size = formatRequiredModelSize(bytesPerSecond);
  return size ? `${size}/s` : null;
}

function bypassedLoraModelKeys(
  packageData: WorkflowPackageResponse | null,
  inputValues: Record<string, unknown>,
): Set<string> {
  const nodeIds = new Set(
    (packageData?.inputs ?? [])
      .filter((input) => input.control === "lora_loader" && input.binding.input_name === "lora_name")
      .filter((input) => isLoraNoneValue(inputValues[input.id]))
      .map((input) => input.binding.node_id),
  );
  if (nodeIds.size === 0) return new Set();
  return new Set(
    (packageData?.required_models ?? [])
      .filter((model) => model.node_id && nodeIds.has(model.node_id))
      .filter((model) => model.input_name == null || model.input_name === "lora_name")
      .filter((model) => model.folder === "loras" || model.model_type === "lora")
      .map(requiredModelKey),
  );
}

function isLoraNoneValue(value: unknown): boolean {
  return typeof value === "string" && value.trim().toLowerCase() === "none";
}

function isEmptyWorkflowValue(value: unknown): boolean {
  return value == null || (typeof value === "string" && value.trim() === "");
}

function requiredModelKey(model: { folder: string; filename: string }): string {
  return `${model.folder}/${model.filename}`.toLowerCase();
}

const retryableRequiredModelStatuses = new Set([
  "missing",
  "download_failed",
  "authentication_required",
  "rate_limited",
  "hash_mismatch",
  "verification_failed",
  "not_enough_disk_space",
]);
