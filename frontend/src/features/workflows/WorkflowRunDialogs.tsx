import { useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Clipboard,
  Loader2,
  RotateCcw,
  SlidersHorizontal,
  X,
} from "lucide-react";

import {
  fetchWorkflowInstallDeveloperDetails,
  type DiagnosticEvent,
  type WorkflowPackageResponse,
  type WorkflowStatusResponse,
} from "../../lib/api/noofyApi";
import {
  emptyComfyUiFailureLogsMessage,
  isMemoryFailureCode,
  MEMORY_FAILURE_MESSAGE,
  MemoryFailureSteps,
  MemoryRequirementSummary,
} from "./workflowMemoryStatus";
import { preparationPhaseStatusLabel } from "./workflowPreparationStatus";
import type {
  FailedTrackedRun,
  RunFailureDialogState,
  RunInputErrorDialogState,
  RunPreparationDialogState,
} from "./workflowRunStateTypes";

export function WorkflowMissingPanel({ onBack }: { onBack: () => void }) {
  return (
    <section className="page-heading page-heading--compact" aria-labelledby="workflow-missing-title">
      <div>
        <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden="true" />
          Back to Home
        </button>
        <p className="eyebrow">Workflow unavailable</p>
        <h1 id="workflow-missing-title">Workflow not installed</h1>
        <p>This workflow is not available on the current Noofy server.</p>
      </div>
    </section>
  );
}

export function DashboardSetupRequired({
  workflowName,
  onBack,
  onContinue,
}: {
  workflowName: string;
  onBack: () => void;
  onContinue?: () => void;
}) {
  return (
    <section className="page-heading page-heading--compact" aria-labelledby="dashboard-setup-required-title">
      <div>
        <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden="true" />
          Back to Home
        </button>
        <p className="eyebrow">Dashboard setup</p>
        <h1 id="dashboard-setup-required-title">Finish {workflowName}</h1>
        <p>This workflow needs dashboard widgets before it can be opened and run.</p>
      </div>
      {onContinue ? (
        <button className="primary-button" type="button" onClick={onContinue}>
          <SlidersHorizontal size={16} aria-hidden="true" />
          Continue setup
        </button>
      ) : null}
    </section>
  );
}

export function packageNeedsDashboardSetup(
  packageData: WorkflowPackageResponse,
  workflowSummary: WorkflowStatusResponse["workflow"] | null | undefined,
) {
  if (workflowSummary?.dashboard_ready === false) return true;
  if (workflowSummary?.dashboard_status && workflowSummary.dashboard_status !== "configured") return true;
  if ((workflowSummary?.unresolved_input_count ?? 0) > 0) return true;
  if (workflowSummary?.status === "needs_input_setup" || workflowSummary?.status === "prepared_needs_input_setup") {
    return true;
  }
  return (
    packageData.dashboard.status !== "configured" ||
    !packageData.dashboard.sections.some((section) => section.controls.length > 0)
  );
}

export function WorkflowRefreshRequiredDialog({
  message,
  onRefresh,
}: {
  message: string;
  onRefresh: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-refresh-required-title">
      <section className="workflow-refresh-required-modal">
        <header className="workflow-refresh-required-modal__header">
          <div className="workflow-refresh-required-modal__icon" aria-hidden="true">
            <RotateCcw size={22} />
          </div>
          <div>
            <p className="eyebrow">Workflow session</p>
            <h2 id="workflow-refresh-required-title">Reload this workflow</h2>
            <p>{message}</p>
          </div>
        </header>
        <footer className="workflow-refresh-required-modal__footer">
          <button className="primary-button primary-button--compact" type="button" onClick={onRefresh}>
            <RotateCcw size={15} aria-hidden="true" />
            Reload workflow
          </button>
        </footer>
      </section>
    </div>
  );
}

export function RunPreparationDialog({
  dialog,
  workflowId,
  workflowName,
  onClose,
}: {
  dialog: RunPreparationDialogState;
  workflowId: string;
  workflowName: string;
  onClose: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [details, setDetails] = useState<Record<string, unknown> | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);

  async function toggleDeveloperDetails() {
    if (detailsOpen) {
      setDetailsOpen(false);
      return;
    }
    setDetailsOpen(true);
    if (details || detailsLoading) return;
    setDetailsLoading(true);
    setDetailsError(null);
    try {
      const response = await fetchWorkflowInstallDeveloperDetails(workflowId);
      setDetails(response.developer_details);
    } catch (error) {
      setDetailsError(error instanceof Error ? error.message : "Developer details could not be loaded.");
    } finally {
      setDetailsLoading(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-preparation-title">
      <section className="workflow-preparation-modal">
        <header className="workflow-preparation-modal__header">
          <div>
            <p className="eyebrow">Workflow run</p>
            <h2 id="workflow-preparation-title">
              {dialog.failed ? "Couldn't set up this workflow" : "Setting up workflow"}
            </h2>
            <p>{dialog.message}</p>
            <p className="workflow-preparation-modal__detail">{workflowName}</p>
          </div>
          <div className="workflow-preparation-modal__header-actions">
            {dialog.failed ? <AlertCircle size={22} aria-hidden="true" /> : <Loader2 className="spin" size={22} aria-hidden="true" />}
            {dialog.failed ? (
              <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
                <X size={18} aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </header>

        <ol className="workflow-preparation-steps" aria-label="Preparation progress">
          {dialog.phases.map((phase) => (
            <li key={phase.id} className={`workflow-preparation-step workflow-preparation-step--${phase.status}`}>
              <span className="workflow-preparation-step__icon" aria-hidden="true">
                {phase.status === "passed" ? (
                  <CheckCircle2 size={16} />
                ) : phase.status === "failed" || phase.status === "blocked" ? (
                  <AlertCircle size={16} />
                ) : phase.status === "active" ? (
                  <Loader2 className="spin" size={16} />
                ) : (
                  <span />
                )}
              </span>
              <span>{phase.label}</span>
              <span className="workflow-preparation-step__status">{preparationPhaseStatusLabel(phase.status)}</span>
            </li>
          ))}
        </ol>

        {dialog.detail ? <p className="workflow-preparation-modal__detail">{dialog.detail}</p> : null}
        {dialog.failed && dialog.developerDetailsAvailable ? (
          <div className="workflow-preparation-modal__developer-details">
            <button className="secondary-button secondary-button--small" type="button" onClick={() => void toggleDeveloperDetails()}>
              {detailsOpen ? "Hide developer details" : "Developer details"}
            </button>
            {detailsOpen ? (
              <section aria-label="Developer details">
                {detailsLoading ? <p>Loading details...</p> : null}
                {detailsError ? <p>{detailsError}</p> : null}
                {details ? <pre>{JSON.stringify(details, null, 2)}</pre> : null}
              </section>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

export function WorkflowFailureDialog({
  dialog,
  workflowId,
  workflowName,
  onClose,
  onToggleDetails,
  onViewLogs,
  onCopy,
  onCopyLogs,
}: {
  dialog: RunFailureDialogState;
  workflowId: string;
  workflowName: string;
  onClose: () => void;
  onToggleDetails: () => void;
  onViewLogs: () => void;
  onCopy: () => void;
  onCopyLogs: () => void;
}) {
  const memoryFailure = isMemoryFailureCode(dialog.errorCode);
  const failureMessage = memoryFailure
    ? dialog.userMessage ?? MEMORY_FAILURE_MESSAGE
    : dialog.userMessage ?? "The run stopped before it finished.";
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-failure-title">
      <section className="workflow-failure-modal">
        <header className="workflow-failure-modal__header">
          <div>
            <p className="eyebrow">Workflow run</p>
            <h2 id="workflow-failure-title">
              {memoryFailure ? "Not enough memory to run this workflow" : "Run stopped"}
            </h2>
            <p>{failureMessage}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workflow-failure-modal__body">
          <div className="workflow-failure-modal__meta">
            <span>Workflow: {workflowName}</span>
          </div>
          {memoryFailure ? <MemoryRequirementSummary requirement={dialog.memoryRequirement} /> : null}
          {memoryFailure ? <MemoryFailureSteps requirement={dialog.memoryRequirement} /> : null}
          <section className="workflow-input-error-details" aria-label="Developer details">
            <button className="ghost-button workflow-input-error-details__toggle" type="button" onClick={onToggleDetails}>
              {dialog.detailsOpen ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}
              Developer details
            </button>
            {dialog.detailsOpen ? (
              <>
                <pre className="workflow-log-section__content workflow-input-error-details__content">
                  {JSON.stringify(
                    {
                      workflow: workflowName,
                      workflow_id: workflowId,
                      job_id: dialog.jobId,
                      user_message: dialog.userMessage,
                      technical_error: dialog.errorMessage,
                      error_code: dialog.errorCode,
                      developer_details: dialog.developerDetails,
                    },
                    null,
                    2,
                  )}
                </pre>
                <button className="secondary-button secondary-button--small" type="button" onClick={onCopy}>
                  <Clipboard size={16} aria-hidden="true" />
                  {dialog.copied ? "Developer report copied" : "Copy developer report"}
                </button>
              </>
            ) : null}
          </section>
          {dialog.logError ? (
            <div className="notice notice--warning notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Logs could not be loaded</strong>
                <span>{dialog.logError}</span>
              </div>
            </div>
          ) : null}
          {dialog.logsLoaded || dialog.logsLoading ? (
            <>
              <DiagnosticLogSection
                title="ComfyUI engine logs"
                events={dialog.comfyuiLogs}
                loading={dialog.logsLoading}
                emptyMessage={emptyComfyUiFailureLogsMessage(dialog.errorCode)}
              />
              <DiagnosticLogSection
                title="Noofy logs"
                events={dialog.noofyLogs}
                loading={dialog.logsLoading}
                emptyMessage="No Noofy logs were returned for this failure."
              />
            </>
          ) : null}
        </div>

        <footer className="workflow-failure-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <div className="workflow-input-error-modal__actions">
            <button className="secondary-button" type="button" onClick={onViewLogs}>
              {dialog.logsLoaded ? "Refresh logs" : "View logs"}
            </button>
            <button className="secondary-button" type="button" onClick={onCopyLogs}>
              <Clipboard size={16} aria-hidden="true" />
              {dialog.logsCopied ? "Logs copied" : "Copy logs"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

export function WorkflowInputErrorDialog({
  dialog,
  workflowId,
  workflowName,
  onClose,
  onFixInput,
  onToggleDetails,
  onViewLogs,
  onCopy,
}: {
  dialog: RunInputErrorDialogState;
  workflowId: string;
  workflowName: string;
  onClose: () => void;
  onFixInput: () => void;
  onToggleDetails: () => void;
  onViewLogs: () => void;
  onCopy: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-input-error-title">
      <section className="workflow-failure-modal workflow-input-error-modal">
        <header className="workflow-failure-modal__header">
          <div>
            <p className="eyebrow">Workflow run</p>
            <h2 id="workflow-input-error-title">{dialog.error.title}</h2>
            <p>{dialog.error.user_message}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workflow-failure-modal__body">
          <div className="workflow-failure-modal__meta">
            <span>Workflow: {workflowName}</span>
          </div>
          <section className="workflow-input-error-details" aria-label="Developer details">
            <button className="ghost-button workflow-input-error-details__toggle" type="button" onClick={onToggleDetails}>
              {dialog.detailsOpen ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}
              Developer details
            </button>
            {dialog.detailsOpen ? (
              <pre className="workflow-log-section__content workflow-input-error-details__content">
                {JSON.stringify(
                  {
                    code: dialog.error.code,
                    message: dialog.error.message,
                    control_id: dialog.error.control_id,
                    input_id: dialog.error.input_id,
                    input_type: dialog.error.input_type,
                    developer_details: dialog.error.developer_details,
                  },
                  null,
                  2,
                )}
              </pre>
            ) : null}
          </section>
          {dialog.logError ? (
            <div className="notice notice--warning notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Logs could not be loaded</strong>
                <span>{dialog.logError}</span>
              </div>
            </div>
          ) : null}
          {dialog.logsLoaded || dialog.logsLoading ? (
            <>
              <DiagnosticLogSection
                title="ComfyUI engine logs"
                events={dialog.comfyuiLogs}
                loading={dialog.logsLoading}
                emptyMessage="No ComfyUI engine logs were returned for this run."
              />
              <DiagnosticLogSection
                title="Noofy logs"
                events={dialog.noofyLogs}
                loading={dialog.logsLoading}
                emptyMessage="No Noofy logs were returned for this run."
              />
            </>
          ) : null}
        </div>

        <footer className="workflow-failure-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <div className="workflow-input-error-modal__actions">
            <button className="secondary-button" type="button" onClick={onViewLogs}>
              {dialog.logsLoaded ? "Refresh logs" : "View logs"}
            </button>
            <button className="secondary-button" type="button" onClick={onCopy}>
              <Clipboard size={16} aria-hidden="true" />
              {dialog.copied ? "Copied" : "Copy details"}
            </button>
            {dialog.error.control_id ? (
              <button className="primary-button" type="button" onClick={onFixInput}>
                Fix input
              </button>
            ) : null}
          </div>
        </footer>
      </section>
    </div>
  );
}

export function BatchFailureSummary({
  failedRuns,
  expanded,
  onToggle,
  onOpenLogs,
}: {
  failedRuns: FailedTrackedRun[];
  expanded: boolean;
  onToggle: () => void;
  onOpenLogs: (run: FailedTrackedRun) => void;
}) {
  return (
    <div className="batch-failure-summary" role="status">
      <AlertCircle size={16} aria-hidden="true" />
      <strong>{failedRuns.length} runs failed</strong>
      <button className="secondary-button secondary-button--small" type="button" onClick={onToggle}>
        Details
      </button>
      {expanded ? (
        <div className="batch-failure-summary__details">
          {failedRuns.map((run) => (
            <div className="batch-failure-summary__row" key={run.handle}>
              <span>{run.message}</span>
              <button className="ghost-button" type="button" onClick={() => onOpenLogs(run)}>
                Logs
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function WorkflowCancelConfirmation({
  count,
  onCancel,
  onConfirm,
}: {
  count: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-cancel-title">
      <section className="workflow-cancel-popover">
        <h2 id="workflow-cancel-title">Cancel {count} runs?</h2>
        <p>This will cancel the current run and all queued runs for this workflow.</p>
        <div className="workflow-cancel-popover__actions">
          <button className="secondary-button" type="button" onClick={onCancel}>
            Keep running
          </button>
          <button className="danger-button" type="button" onClick={onConfirm}>
            Cancel all
          </button>
        </div>
      </section>
    </div>
  );
}

export function splitDiagnosticLogs(events: DiagnosticEvent[]) {
  const comfyuiLogs: DiagnosticEvent[] = [];
  const noofyLogs: DiagnosticEvent[] = [];
  for (const event of events) {
    if (isComfyUIDiagnostic(event)) {
      comfyuiLogs.push(event);
    } else {
      noofyLogs.push(event);
    }
  }
  return { comfyuiLogs, noofyLogs };
}

export function formatFailureReport(workflowId: string, dialog: RunFailureDialogState) {
  return [
    "Workflow failure report",
    `Workflow: ${workflowId}`,
    `Job: ${dialog.jobId ?? "not available"}`,
    `Error: ${dialog.errorMessage}`,
    `Error code: ${dialog.errorCode ?? "none"}`,
    dialog.userMessage ? `User message: ${dialog.userMessage}` : null,
    "",
    "Developer details",
    JSON.stringify(dialog.developerDetails, null, 2),
    "",
    "ComfyUI engine logs",
    formatDiagnosticEvents(dialog.comfyuiLogs) || (dialog.logsLoaded ? emptyComfyUiFailureLogsMessage(dialog.errorCode) : "Logs were not loaded."),
    "",
    "Noofy logs",
    formatDiagnosticEvents(dialog.noofyLogs) || (dialog.logsLoaded ? "No Noofy logs were returned for this failure." : "Logs were not loaded."),
  ].filter((line): line is string => line !== null).join("\n");
}

export function formatFailureLogsReport({
  comfyuiLogs,
  noofyLogs,
  logsLoaded,
  errorCode,
}: Pick<RunFailureDialogState, "comfyuiLogs" | "noofyLogs" | "logsLoaded" | "errorCode">) {
  return [
    "ComfyUI engine logs",
    formatDiagnosticEvents(comfyuiLogs) || (logsLoaded ? emptyComfyUiFailureLogsMessage(errorCode) : "Logs were not loaded."),
    "",
    "Noofy logs",
    formatDiagnosticEvents(noofyLogs) || (logsLoaded ? "No Noofy logs were returned for this failure." : "Logs were not loaded."),
  ].join("\n");
}

export function formatInputErrorReport(workflowId: string, dialog: RunInputErrorDialogState) {
  return [
    "Workflow input error report",
    `Workflow: ${workflowId}`,
    `Title: ${dialog.error.title}`,
    `Message: ${dialog.error.user_message}`,
    "",
    "Developer details",
    JSON.stringify(
      {
        code: dialog.error.code,
        message: dialog.error.message,
        control_id: dialog.error.control_id,
        input_id: dialog.error.input_id,
        input_type: dialog.error.input_type,
        developer_details: dialog.error.developer_details,
      },
      null,
      2,
    ),
    "",
    "ComfyUI engine logs",
    formatDiagnosticEvents(dialog.comfyuiLogs) || "Logs were not loaded.",
    "",
    "Noofy logs",
    formatDiagnosticEvents(dialog.noofyLogs) || "Logs were not loaded.",
  ].join("\n");
}

export function focusDashboardControl(controlId: string) {
  const selector = `[data-dashboard-control-id="${escapeCssIdentifier(controlId)}"]`;
  const target = document.querySelector<HTMLElement>(selector);
  if (!target) return;
  target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
  target.classList.add("dashboard-control-target--highlight");
  const focusTarget = target.matches("input, textarea, select, button")
    ? target
    : target.querySelector<HTMLElement>("input, textarea, select, button, [tabindex]:not([tabindex='-1'])");
  window.setTimeout(() => focusTarget?.focus({ preventScroll: true }), 250);
  window.setTimeout(() => target.classList.remove("dashboard-control-target--highlight"), 1800);
}

function DiagnosticLogSection({
  title,
  events,
  loading,
  emptyMessage,
}: {
  title: string;
  events: DiagnosticEvent[];
  loading: boolean;
  emptyMessage: string;
}) {
  return (
    <section className="workflow-log-section" aria-labelledby={sectionId(title)}>
      <div className="workflow-log-section__header">
        <h3 id={sectionId(title)}>{title}</h3>
        <span>{loading ? "Loading" : `${events.length} events`}</span>
      </div>
      <pre className="workflow-log-section__content">
        {loading ? "Loading logs..." : events.length > 0 ? formatDiagnosticEvents(events) : emptyMessage}
      </pre>
    </section>
  );
}

function formatDiagnosticEvents(events: DiagnosticEvent[]) {
  return events.map(formatDiagnosticEvent).join("\n");
}

function formatDiagnosticEvent(event: DiagnosticEvent) {
  const details = event.details && Object.keys(event.details).length > 0
    ? ` details=${JSON.stringify(event.details)}`
    : "";
  const ids = [
    event.workflow_id ? `workflow=${event.workflow_id}` : null,
    event.job_id ? `job=${event.job_id}` : null,
  ].filter(Boolean).join(" ");
  return `[${event.timestamp}] ${event.level.toUpperCase()} ${event.source}${ids ? ` ${ids}` : ""}: ${event.message}${details}`;
}

function sectionId(title: string) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function isComfyUIDiagnostic(event: DiagnosticEvent) {
  const source = event.source.toLowerCase();
  if (
    source.startsWith("comfyui.") ||
    source.startsWith("runtime.comfyui_") ||
    source === "runtime.manager" ||
    source === "runtime.environment" ||
    source === "runtime.environment.stdout" ||
    source === "runtime.environment.stderr" ||
    source === "runtime.runner_process" ||
    source === "runtime.runner_process.stdout" ||
    source === "runtime.runner_coordinator"
  ) {
    return true;
  }

  if (isKnownNoofyDiagnosticSource(source)) {
    return false;
  }

  const searchable = `${event.source} ${event.message} ${JSON.stringify(event.details ?? {})}`.toLowerCase();
  return (
    searchable.includes("comfyui") ||
    searchable.includes("runner_id") ||
    searchable.includes("base_url") ||
    searchable.includes("ws_url") ||
    searchable.includes("\"pid\"") ||
    searchable.includes("returncode") ||
    searchable.includes("managed engine") ||
    searchable.includes("engine startup") ||
    searchable.includes("engine crash")
  );
}

function isKnownNoofyDiagnosticSource(source: string) {
  return (
    source === "engine.service" ||
    source.startsWith("runs.") ||
    source === "memory_governor" ||
    source.startsWith("workflow.") ||
    source === "runtime.workspace" ||
    source === "runtime.node_registry" ||
    source.startsWith("runtime.dependency_") ||
    source === "runtime.install_transaction" ||
    source === "runtime.install_state" ||
    source === "runtime.storage_gc" ||
    source === "capsule.installer" ||
    source.startsWith("settings.") ||
    source === "trust" ||
    source.startsWith("trust.")
  );
}

function escapeCssIdentifier(value: string) {
  if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, "\\$&");
}
