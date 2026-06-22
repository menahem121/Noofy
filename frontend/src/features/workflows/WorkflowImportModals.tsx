import {
  AlertCircle,
  ArrowRight,
  Download,
  ExternalLink,
  Loader2,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  type ImportModelDownloadJobStatus,
  type ImportModelVerificationJobStatus,
  type RequiredModelAvailability,
  type WorkflowImportResponse,
} from "../../lib/api/noofyApi";
import { openExternalUrl } from "../../lib/openExternalUrl";
import {
  failedModelMessage,
  isModelDownloadActive,
  isModelDownloadFailure,
  modelDownloadPanelTone,
  modelDownloadPercentLabel,
} from "../../lib/modelDownloadProgress";
import { workflowDisplayName } from "../../lib/workflowNames";
import { ModelReferenceDetails } from "./ModelReferenceDetails";
import { ModelVerificationProgressPanel } from "./ModelVerificationProgressPanel";
import { requiredModelTypeLabel } from "./requiredModelLabels";
import { importNeedsCustomNodeResolution, type WorkflowImportFlowController } from "./useWorkflowImportFlow";
import { importNeedsConfiguration } from "./workflowImportUtils";

export function WorkflowImportDialogs({
  importFlow,
  onViewModels,
}: {
  importFlow: WorkflowImportFlowController;
  onViewModels: () => void;
}) {
  const { state } = importFlow;
  const needsCustomNodeResolution = Boolean(
    state.pendingImport && importNeedsCustomNodeResolution(state.pendingImport),
  );
  return (
    <>
      {state.pendingImport?.duplicate_identity && !state.pendingImport.model_summary && !needsCustomNodeResolution ? (
        <DuplicateWorkflowModal
          importResult={state.pendingImport}
          busy={state.importing}
          onReplace={() => void importFlow.duplicateImport("replace")}
          onCopy={() => void importFlow.duplicateImport("copy")}
          onCancel={() => void importFlow.cancelImport()}
        />
      ) : null}
      {needsCustomNodeResolution && state.pendingImport ? (
        <RequiredCustomNodesModal
          importResult={state.pendingImport}
          busy={state.importing}
          onResolveUrls={(urls) => void importFlow.resolveCustomNodesFromUrls(urls)}
          onApproveCandidate={(candidateId) => void importFlow.approveCustomNodeCandidate(candidateId)}
          onNoCustomNodes={() => void importFlow.markWorkflowHasNoCustomNodes()}
          onCancel={() => void importFlow.cancelImport()}
        />
      ) : null}
      {state.pendingImport?.model_summary && !needsCustomNodeResolution ? (
        <RequiredModelsModal
          importResult={state.pendingImport}
          busy={state.importing || state.downloadingModels}
          importing={state.importing}
          downloadJob={state.downloadJob}
          verificationJob={state.verificationJob}
          onDownload={() => void importFlow.downloadMissingModels()}
          onCancelDownload={() => void importFlow.cancelModelDownload()}
          onContinue={() => void importFlow.continueImport()}
          onReplace={() => void importFlow.duplicateImport("replace")}
          onCopy={() => void importFlow.duplicateImport("copy")}
          onReadyAction={() => void importFlow.readyImportAction()}
          onCancel={() => void importFlow.cancelImport()}
          onViewModels={onViewModels}
        />
      ) : null}
    </>
  );
}

export function RequiredCustomNodesModal({
  importResult,
  busy,
  onResolveUrls,
  onApproveCandidate,
  onNoCustomNodes,
  onCancel,
}: {
  importResult: WorkflowImportResponse;
  busy: boolean;
  onResolveUrls: (urlsByNodeType: Record<string, string>) => void;
  onApproveCandidate: (candidateId: string) => void;
  onNoCustomNodes: () => void;
  onCancel: () => void;
}) {
  const resolution = importResult.custom_node_resolution;
  const fields = resolution?.github_url_fields ?? [];
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [showManualUrl, setShowManualUrl] = useState(false);
  const allNodeTypes = useMemo(() => {
    const ambiguous = resolution?.ambiguous_node_types.map((item) => item.node_type) ?? [];
    const packaged = resolution?.missing_custom_node?.node_types ?? [];
    const fieldNodeTypes = resolution?.github_url_fields.map((field) => field.node_type) ?? [];
    return [
      ...new Set([...(resolution?.unresolved_node_types ?? []), ...ambiguous, ...packaged, ...fieldNodeTypes]),
    ].sort();
  }, [
    resolution?.ambiguous_node_types,
    resolution?.github_url_fields,
    resolution?.missing_custom_node?.node_types,
    resolution?.unresolved_node_types,
  ]);
  if (!resolution) return null;
  const needsComfyUpdate = resolution.status === "needs_comfyui_update";
  const candidate = resolution.mode === "candidate_approval" && !showManualUrl ? resolution.candidate : null;
  const missingName =
    resolution.missing_custom_node?.package_id ??
    resolution.package_id ??
    fields[0]?.label ??
    allNodeTypes[0] ??
    "Workflow extension";
  const canDownload = fields.length > 0 && repositoryUrl.trim().length > 0;
  const submitRepositoryUrl = () => {
    const url = repositoryUrl.trim();
    onResolveUrls(Object.fromEntries(fields.map((field) => [field.node_type, url])));
  };
  const headerCopy = candidate
    ? "Confirm the repository Noofy found, or enter a different GitHub URL."
    : needsComfyUpdate
      ? resolution.update_guidance ?? "Update managed ComfyUI from Settings, then check this workflow again."
      : "Paste the repository's GitHub URL so Noofy can prepare this workflow.";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="required-custom-nodes-title">
      <section className="required-models-modal" aria-busy={busy}>
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Required custom node</p>
            <h2 id="required-custom-nodes-title">
              {needsComfyUpdate ? "Update ComfyUI to continue" : "Add the missing custom node"}
            </h2>
            <p>{headerCopy}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Cancel import" disabled={busy} onClick={onCancel}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-modal__body">
          {candidate ? (
            <article className="custom-node-row custom-node-row--candidate">
              <div className="custom-node-row__title">
                <strong>{candidate.owner}/{candidate.repo}</strong>
                <span>{candidate.description || "GitHub repository"}</span>
              </div>
              <CustomNodeTypes nodeTypes={allNodeTypes} />
              <button
                className="secondary-button"
                type="button"
                disabled={busy}
                onClick={() => void openExternalUrl(candidate.repo_url)}
              >
                <ExternalLink size={14} aria-hidden="true" />
                Open Repository
              </button>
            </article>
          ) : (
            <article className="custom-node-row custom-node-row--repository">
              <div className="custom-node-row__title">
                <strong>{missingName}</strong>
              </div>
              <CustomNodeTypes nodeTypes={allNodeTypes} />
              <label className="custom-node-row__url">
                <span>GitHub repository URL</span>
                <input
                  type="url"
                  value={repositoryUrl}
                  placeholder="https://github.com/owner/repository"
                  disabled={busy || needsComfyUpdate}
                  onChange={(event) => setRepositoryUrl(event.target.value)}
                />
              </label>
            </article>
          )}

          {!needsComfyUpdate ? (
            <p className="custom-node-trust-note">
              Only continue with a repository you trust. Noofy installs it only for this workflow.
            </p>
          ) : null}

          {busy ? (
            <div className="required-models-modal__processing" role="status" aria-live="polite">
              <Loader2 className="spin" size={16} aria-hidden="true" />
              <span>Checking custom nodes...</span>
            </div>
          ) : null}
        </div>

        <footer className="required-models-modal__footer">
          {candidate ? (
            <>
              <button
                className="primary-button"
                type="button"
                disabled={busy}
                onClick={() => onApproveCandidate(candidate.candidate_id)}
              >
                {busy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
                Use this repo
              </button>
              <button className="secondary-button" type="button" disabled={busy} onClick={() => setShowManualUrl(true)}>
                Enter another GitHub URL manually
              </button>
            </>
          ) : (
            <>
              <button
                className="primary-button"
                type="button"
                disabled={busy || needsComfyUpdate || !canDownload}
                onClick={submitRepositoryUrl}
              >
                {busy && !needsComfyUpdate ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
                Use GitHub URL
              </button>
              {needsComfyUpdate ? (
                <button className="secondary-button" type="button" disabled={busy} onClick={onNoCustomNodes}>
                  Check after ComfyUI update
                </button>
              ) : null}
            </>
          )}
          <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
            Cancel Import
          </button>
        </footer>
      </section>
    </div>
  );
}

function CustomNodeTypes({ nodeTypes }: { nodeTypes: string[] }) {
  if (nodeTypes.length === 0) return null;
  return (
    <div className="custom-node-types">
      <span>Nodes used by this workflow</span>
      <ul>
        {nodeTypes.map((nodeType) => (
          <li key={nodeType}>{nodeType}</li>
        ))}
      </ul>
    </div>
  );
}

export function DuplicateWorkflowModal({
  importResult,
  busy,
  onReplace,
  onCopy,
  onCancel,
}: {
  importResult: WorkflowImportResponse;
  busy: boolean;
  onReplace: () => void;
  onCopy: () => void;
  onCancel: () => void;
}) {
  const duplicate = importResult.duplicate_identity;
  if (!duplicate) return null;
  const existingName = workflowDisplayName(duplicate.existing_workflow ?? importResult.workflow);
  const incomingName = workflowDisplayName(importResult.workflow);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="duplicate-import-title">
      <section className="required-models-modal" aria-busy={busy}>
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Workflow already exists</p>
            <h2 id="duplicate-import-title">{incomingName}</h2>
            <p>
              Noofy already has {existingName}. Choose whether to replace that local workflow, import this file as a
              separate copy, or cancel.
            </p>
          </div>
          <button className="icon-button" type="button" aria-label="Cancel import" disabled={busy} onClick={onCancel}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-modal__body">
          <div className="notice notice--warning" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <div>
              <strong>Replacing resets local setup</strong>
              <span>Replacing this workflow clears any saved inputs, layout changes, output preferences, and setup state tied to the older copy.</span>
            </div>
          </div>

          {busy ? (
            <div className="required-models-modal__processing" role="status" aria-live="polite">
              <Loader2 className="spin" size={16} aria-hidden="true" />
              <span>Importing workflow...</span>
            </div>
          ) : null}
        </div>

        <footer className="required-models-modal__footer required-models-modal__footer--ready">
          <button className="primary-button" type="button" disabled={busy} onClick={onReplace}>
            {busy ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
            {busy ? "Replacing..." : "Replace Existing Workflow"}
          </button>
          <button className="secondary-button" type="button" disabled={busy} onClick={onCopy}>
            Import as Copy
          </button>
          <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
            Cancel Import
          </button>
        </footer>
      </section>
    </div>
  );
}

export function RequiredModelsModal({
  importResult,
  busy,
  importing,
  downloadJob,
  verificationJob,
  onDownload,
  onCancelDownload,
  onContinue,
  onReplace,
  onCopy,
  onReadyAction,
  onCancel,
  onViewModels,
}: {
  importResult: WorkflowImportResponse;
  busy: boolean;
  importing: boolean;
  downloadJob: ImportModelDownloadJobStatus | null;
  verificationJob: ImportModelVerificationJobStatus | null;
  onDownload: () => void;
  onCancelDownload: () => void;
  onContinue: () => void;
  onReplace: () => void;
  onCopy: () => void;
  onReadyAction: () => void;
  onCancel: () => void;
  onViewModels: () => void;
}) {
  const summary = verificationJob?.model_summary ?? downloadJob?.model_summary ?? importResult.model_summary;
  if (!summary) return null;
  const duplicate = importResult.duplicate_identity;
  const retryableStatuses = new Set([
    "missing",
    "download_failed",
    "authentication_required",
    "rate_limited",
    "hash_mismatch",
    "verification_failed",
    "not_enough_disk_space",
  ]);
  const hasDownloadable = summary.models.some((model) => isRequiredModelDownloadRetryable(model, retryableStatuses));
  const activeDownload = isModelDownloadActive(downloadJob?.status);
  const terminalVerification = verificationJob?.status === "completed" || verificationJob?.status === "failed";
  const activeVerification =
    verificationJob?.status === "queued" ||
    verificationJob?.status === "running" ||
    (!terminalVerification && summary.models.some((model) => model.status === "checking"));
  const jobModels = new Map(
    activeDownload ? downloadJob?.models.map((model) => [model.requirement_id, model]) ?? [] : [],
  );
  const readyToRun = summary.ready_to_run && !activeDownload && !activeVerification;
  const needsWorkflowConfiguration = importNeedsConfiguration(importResult);
  const readyActionLabel = needsWorkflowConfiguration ? "Configure Workflow" : "Open Workflow";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="required-models-title">
      <section className="required-models-modal" aria-busy={importing}>
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Required models</p>
            <h2 id="required-models-title">{workflowDisplayName(importResult.workflow)}</h2>
            <p>
              Noofy checks your computer for matching model files first. If anything is missing, you can download it before opening this workflow.
            </p>
          </div>
          <button className="icon-button" type="button" aria-label="Cancel import" disabled={busy} onClick={onCancel}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="required-models-modal__body">
          {duplicate ? (
            <div className="notice notice--warning" role="status">
              <AlertCircle size={18} aria-hidden="true" />
              <div>
                <strong>Workflow already exists</strong>
                <span>{duplicate.user_facing_message}</span>
              </div>
            </div>
          ) : null}

          <div className="required-models-list">
            {summary.models.map((model) => (
              <RequiredModelRow key={model.requirement_id} model={model} progress={jobModels.get(model.requirement_id)} />
            ))}
          </div>

          {downloadJob && shouldShowDownloadProgress(downloadJob) ? (
            <ModelDownloadProgressPanel job={downloadJob} onRetry={onDownload} onViewModels={onViewModels} />
          ) : null}
          {activeVerification ? <ModelVerificationProgressPanel job={verificationJob} /> : null}

          {importing ? (
            <div className="required-models-modal__processing" role="status" aria-live="polite">
              <Loader2 className="spin" size={16} aria-hidden="true" />
              <span>Importing workflow...</span>
            </div>
          ) : null}
        </div>

        <footer className={`required-models-modal__footer${readyToRun ? " required-models-modal__footer--ready" : ""}`}>
          {duplicate ? (
            <>
              {!readyToRun ? (
                <button className="primary-button" type="button" disabled={busy || activeVerification || !hasDownloadable} onClick={onDownload}>
                  <Download size={16} aria-hidden="true" />
                  {activeDownload ? "Downloading..." : "Download Missing Models"}
                </button>
              ) : null}
              {activeDownload ? (
                <button className="secondary-button" type="button" onClick={onCancelDownload}>
                  Cancel Download
                </button>
              ) : null}
              <button
                className={readyToRun ? "primary-button" : "secondary-button"}
                type="button"
                disabled={busy || activeVerification}
                onClick={onReplace}
              >
                {importing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
                {importing ? "Replacing..." : "Replace Existing Workflow"}
              </button>
              <button className="secondary-button" type="button" disabled={busy || activeVerification} onClick={onCopy}>
                Import as Copy
              </button>
              <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
                Cancel Import
              </button>
            </>
          ) : readyToRun ? (
            <button className="primary-button" type="button" disabled={busy} onClick={onReadyAction}>
              {importing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : <ArrowRight size={16} aria-hidden="true" />}
              {importing ? "Finishing import..." : readyActionLabel}
            </button>
          ) : (
            <>
              <button className="primary-button" type="button" disabled={busy || activeVerification || !hasDownloadable} onClick={onDownload}>
                <Download size={16} aria-hidden="true" />
                {activeDownload ? "Downloading..." : "Download Missing Models"}
              </button>
              {activeDownload ? (
                <button className="secondary-button" type="button" onClick={onCancelDownload}>
                  Cancel Download
                </button>
              ) : null}
              <button className="secondary-button" type="button" disabled={busy || activeVerification} onClick={onContinue}>
                {importing ? <Loader2 className="spin" size={16} aria-hidden="true" /> : null}
                {importing ? "Importing..." : "Continue Without Downloading"}
              </button>
              <button className="ghost-button" type="button" disabled={busy} onClick={onCancel}>
                Cancel Import
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
}

function RequiredModelRow({
  model,
  progress,
}: {
  model: RequiredModelAvailability;
  progress?: ImportModelDownloadJobStatus["models"][number];
}) {
  const status = progress?.status ?? model.status;
  const statusLabel = progress?.status_label ?? model.status_label;
  const message = progress?.message ?? model.message;
  return (
    <article className="required-model-row">
      <div className="required-model-row__main">
        <h3>{model.filename}</h3>
        <p>
          {[requiredModelTypeLabel(model.folder, model.model_type), formatModelSize(model.size_bytes)]
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
        <span className="model-identity">{verificationLabel(model.verification_level)}</span>
        <span className={`model-status-pill model-status-pill--${status}`}>{statusLabel}</span>
        <span className="model-source">{modelSourceLabel(model)}</span>
      </div>
    </article>
  );
}

function ModelDownloadProgressPanel({
  job,
  onRetry,
  onViewModels,
}: {
  job: ImportModelDownloadJobStatus;
  onRetry: () => void;
  onViewModels: () => void;
}) {
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
  const notEnoughDiskSpace = hasNotEnoughDiskSpaceFailure(job);

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
          <div
            className="model-download-progress__bar-fill"
            style={{ width: `${percent}%` }}
          />
        </div>
      ) : null}
      <p>
        {[formatModelSize(job.bytes_downloaded), job.total_bytes ? formatModelSize(job.total_bytes) : null]
          .filter(Boolean)
          .join(" / ")}
        {job.speed_bytes_per_second ? ` · ${formatModelSpeed(job.speed_bytes_per_second)}` : ""}
      </p>
      <span>{job.user_facing_message}</span>
      {failureMessage ? <span className="model-download-progress__failure">{failureMessage}</span> : null}
      {notEnoughDiskSpace ? (
        <button className="secondary-button secondary-button--small" type="button" onClick={onViewModels}>
          View Models
        </button>
      ) : isModelDownloadFailure(job.status) ? (
        <button className="secondary-button secondary-button--small" type="button" onClick={onRetry}>
          Retry Download
        </button>
      ) : null}
    </div>
  );
}

function hasNotEnoughDiskSpaceFailure(job: ImportModelDownloadJobStatus) {
  return (
    job.status === "not_enough_disk_space" ||
    job.models.some((model) => model.status === "not_enough_disk_space") ||
    Boolean(job.model_summary?.models.some((model) => model.status === "not_enough_disk_space"))
  );
}

function shouldShowDownloadProgress(job: ImportModelDownloadJobStatus) {
  if (job.status === "completed") {
    return Boolean(
      job.model_summary?.models.length &&
      job.model_summary.models.every((model) => model.status === "available"),
    );
  }
  if (
    job.status === "pending" ||
    job.status === "queued" ||
    job.status === "running" ||
    job.status === "downloading" ||
    job.status === "verifying" ||
    job.status === "succeeded" ||
    job.status === "failed" ||
    job.status === "completed_with_errors" ||
    job.status === "canceled"
  ) {
    return true;
  }
  return job.percent !== null || job.bytes_downloaded !== null;
}

function formatModelSize(size: number | null) {
  if (!size) return null;
  if (size >= 1024 ** 3) return `${(size / 1024 ** 3).toFixed(1)} GB`;
  if (size >= 1024 ** 2) return `${Math.round(size / 1024 ** 2)} MB`;
  return `${Math.round(size / 1024)} KB`;
}

function formatModelSpeed(bytesPerSecond: number) {
  const size = formatModelSize(bytesPerSecond);
  return size ? `${size}/s` : null;
}

function verificationLabel(level: string) {
  if (level === "sha256_size") return "Verified file";
  if (level === "filename_size") return "Name and size match";
  if (level === "filename_only") return "Name match";
  return "Not verified";
}

function modelSourceLabel(model: RequiredModelAvailability) {
  if (model.source_urls.length > 0) return "Ready to download";
  if (model.source_availability === "resolvable") return "Can search for a download";
  return "No download found";
}

function isRequiredModelDownloadRetryable(model: RequiredModelAvailability, retryableStatuses: Set<string>) {
  return (
    retryableStatuses.has(model.status) ||
    (model.status === "possible_match" && model.source_availability === "resolvable")
  );
}
