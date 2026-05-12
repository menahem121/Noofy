import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Clipboard,
  CheckCircle2,
  Download,
  Image,
  Loader2,
  Play,
  RotateCcw,
  Share2,
  Square,
  X,
} from "lucide-react";

import {
  cancelJob,
  createJobEventsUrl,
  exportWorkflowUrl,
  fetchJobLogs,
  fetchJobProgress,
  fetchJobResult,
  fetchLogs,
  fetchWorkflowModelSummary,
  fetchWorkflowPackage,
  fetchWorkflowStatus,
  isEngineJob,
  resolveBackendUrl,
  runWorkflow,
  uploadDashboardAsset,
  validateWorkflow,
  type DashboardControlDef,
  type DiagnosticEvent,
  type EngineJob,
  type JobProgress,
  type JobResult,
  type MemoryStatus,
  type RequiredModelSummary,
  type WorkflowInputDef,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
  type WorkflowStatusResponse,
  type WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import type {
  DashboardSchema,
  DashboardWidget,
  WidgetGroup,
  WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type { GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { useWorkflowUserState } from "../../lib/useWorkflowUserState";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { CanvasDashboardView } from "./CanvasDashboardView";
import { DashboardInputControl } from "./DashboardInputControl";

interface WorkflowRunPageProps {
  workflowId: string;
  onBack: () => void;
  onEditWidgets?: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RunPageState {
  loading: boolean;
  workflowStatus: WorkflowStatusResponse | null;
  modelSummary: RequiredModelSummary | null;
  packageData: WorkflowPackageResponse | null;
  validation: WorkflowValidationResult | null;
  job: EngineJob | null;
  progress: JobProgress | null;
  result: JobResult | null;
  error: string | null;
}

interface RunFailureDialogState {
  errorMessage: string;
  jobId: string | null;
  logsLoading: boolean;
  logError: string | null;
  comfyuiLogs: DiagnosticEvent[];
  noofyLogs: DiagnosticEvent[];
  copied: boolean;
}

const initialState: RunPageState = {
  loading: true,
  workflowStatus: null,
  modelSummary: null,
  packageData: null,
  validation: null,
  job: null,
  progress: null,
  result: null,
  error: null,
};

const terminalStatuses = new Set(["completed", "failed", "canceled"]);
const watchableJobStatuses = new Set(["queued", "running"]);
const optimisticJobId = "__pending_workflow_run__";
const logLimit = 200;

export function WorkflowRunPage({ workflowId, onBack, onEditWidgets, onNavigate }: WorkflowRunPageProps) {
  const [state, setState] = useState<RunPageState>(initialState);
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [failureDialog, setFailureDialog] = useState<RunFailureDialogState | null>(null);
  const [draftLayoutOverrides, setDraftLayoutOverrides] = useState<Record<string, GridItemLayout> | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);

  const { viewMode } = useAppPreferences();
  const runtimeStatus = useRuntimeStatus();
  const isRunning = isSubmittingRun || state.progress?.status === "queued" || state.progress?.status === "running";
  const isWaitingForMemory = state.job?.status === "queued_pending_memory";
  const isBlockedByMemory = state.job?.status === "blocked_by_memory";
  const status = runtimeStatus.statusView;

  const outputImages = useMemo(() => extractImageUrls(state.result), [state.result]);

  // Build input index from package data.
  const inputIndex = useMemo<Map<string, WorkflowInputDef>>(() => {
    const map = new Map<string, WorkflowInputDef>();
    for (const input of state.packageData?.inputs ?? []) {
      map.set(input.id, input);
    }
    return map;
  }, [state.packageData]);

  // Build output index.
  const outputIndex = useMemo<Map<string, WorkflowOutputDef>>(() => {
    const map = new Map<string, WorkflowOutputDef>();
    for (const output of state.packageData?.outputs ?? []) {
      map.set(output.id, output);
    }
    return map;
  }, [state.packageData]);

  // Collect creator defaults from package data.
  const packageDefaults = useMemo<Record<string, unknown>>(() => {
    const defaults: Record<string, unknown> = {};
    for (const input of state.packageData?.inputs ?? []) {
      defaults[input.id] = input.default;
    }
    return defaults;
  }, [state.packageData]);

  const dashboardVersion = state.packageData?.dashboard?.version ?? "";
  const allControls = useMemo(
    () => state.packageData?.dashboard?.sections.flatMap((section) => section.controls) ?? [],
    [state.packageData],
  );
  const dashboardControlIds = useMemo(() => allControls.map((control) => control.id), [allControls]);

  const {
    values: inputValues,
    setValue: setInputValue,
    restoreDefaults,
    layoutOverrides,
    setLayoutOverride,
    outputPreferences,
    setOutputPreference,
    getOutputPreferencesSnapshot,
  } = useWorkflowUserState(workflowId, packageDefaults, dashboardVersion, inputIndex, dashboardControlIds);

  // Build output-images-by-node-id map for canvas output widgets.
  const outputImagesByNodeId = useMemo<Map<string, string[]>>(() => {
    const map = new Map<string, string[]>();
    if (!state.result) return map;
    for (const output of state.result.outputs) {
      const outputPayload = output.output;
      if (!outputPayload || typeof outputPayload !== "object") continue;
      const nodeIdKey = Object.keys(output).find((k) => k !== "output");
      const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey;
      if (!nodeId) continue;
      const images = (outputPayload as Record<string, unknown>).images;
      if (!Array.isArray(images)) continue;
      const imageUrls: string[] = [];
      for (const image of images) {
        if (image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
          imageUrls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
        }
      }
      if (imageUrls.length > 0) {
        map.set(nodeId, [...(map.get(nodeId) ?? []), ...imageUrls]);
      }
    }
    return map;
  }, [state.result]);

  async function loadRequirements() {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [workflowStatus, packageData, modelSummary] = await Promise.all([
        fetchWorkflowStatus(workflowId).catch(() => null),
        fetchWorkflowPackage(workflowId).catch(() => null),
        fetchWorkflowModelSummary(workflowId).catch(() => null),
      ]);

      const validation = await validateWorkflow(workflowId);
      setState((current) => ({ ...current, loading: false, workflowStatus, modelSummary, packageData, validation }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        workflowStatus: null,
        modelSummary: null,
        packageData: null,
        validation: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  useEffect(() => {
    void runtimeStatus.refreshRuntime({ silent: true });
    void loadRequirements();
    return () => {
      cleanupJobWatchers();
    };
  }, [workflowId, runtimeStatus.refreshRuntime]);

  async function handleRun() {
    if (!canRun) {
      return;
    }

    cleanupJobWatchers();
    setIsSubmittingRun(true);
    setFailureDialog(null);
    setState((current) => ({
      ...current,
      job: null,
      progress: optimisticProgress(),
      result: null,
      error: null,
    }));

    try {
      const response = await runWorkflow(workflowId, {
        inputs: inputValues as Record<string, unknown>,
        options: {},
        output_preferences_snapshot: getOutputPreferencesSnapshot(),
      });

      if (!isEngineJob(response)) {
        setIsSubmittingRun(false);
        setState((current) => ({ ...current, validation: response, progress: null }));
        return;
      }

      setIsSubmittingRun(false);
      setSubmittedJob(response);
      if (isWatchableJob(response)) {
        watchJob(response.job_id);
        await pollJobOnce(response.job_id);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setIsSubmittingRun(false);
      setState((current) => ({
        ...current,
        job: null,
        progress: null,
        result: null,
        error: message,
      }));
      void openFailureDialog(message, null);
    }
  }

  async function handleCancel() {
    if (!state.job) return;
    try {
      const progress = await cancelJob(state.job.job_id);
      cleanupJobWatchers();
      setIsSubmittingRun(false);
      setState((current) => ({ ...current, progress }));
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleImageUpload(inputId: string, file: File) {
    try {
      const { asset_id } = await uploadDashboardAsset(workflowId, file);
      setInputValue(inputId, asset_id);
    } catch {
      // ignore — user will see the input is still empty
    }
  }

  async function openFailureDialog(errorMessage: string, jobId: string | null) {
    setFailureDialog({
      errorMessage,
      jobId,
      logsLoading: true,
      logError: null,
      comfyuiLogs: [],
      noofyLogs: [],
      copied: false,
    });

    try {
      const response = jobId ? await fetchJobLogs(jobId, { limit: logLimit }) : await fetchLogs({ limit: logLimit });
      const splitLogs = splitDiagnosticLogs(response.events);
      setFailureDialog((current) =>
        current && current.errorMessage === errorMessage && current.jobId === jobId
          ? {
              ...current,
              logsLoading: false,
              comfyuiLogs: splitLogs.comfyuiLogs,
              noofyLogs: splitLogs.noofyLogs,
            }
          : current,
      );
    } catch (error) {
      setFailureDialog((current) =>
        current && current.errorMessage === errorMessage && current.jobId === jobId
          ? {
              ...current,
              logsLoading: false,
              logError: error instanceof Error ? error.message : String(error),
            }
          : current,
      );
    }
  }

  async function handleCopyFailureLogs() {
    if (!failureDialog) return;
    await navigator.clipboard.writeText(formatFailureReport(workflowId, failureDialog));
    setFailureDialog((current) => (current ? { ...current, copied: true } : current));
  }

  function watchJob(jobId: string) {
    if (typeof EventSource === "undefined") {
      pollTimerRef.current = window.setInterval(() => {
        void pollJobOnce(jobId);
      }, 1000);
      return;
    }

    const source = new EventSource(createJobEventsUrl(jobId));
    eventSourceRef.current = source;
    source.addEventListener("progress", (event) => {
      const progress = JSON.parse(event.data) as JobProgress;
      if (terminalStatuses.has(progress.status)) setIsSubmittingRun(false);
      setState((current) => ({ ...current, progress }));
    });
    source.addEventListener("result", (event) => {
      source.close();
      eventSourceRef.current = null;
      const result = JSON.parse(event.data) as JobResult | EngineJob;
      if (isEngineJob(result)) {
        setSubmittedJob(result);
        if (isWatchableJob(result)) {
          watchJob(result.job_id);
          void pollJobOnce(result.job_id);
        }
        return;
      }
      setIsSubmittingRun(false);
      setState((current) => ({ ...current, result }));
      if (result.status === "failed") {
        void openFailureDialog(result.error ?? "The local engine could not finish this run.", result.job_id);
      }
    });
    source.onerror = () => {
      source.close();
      eventSourceRef.current = null;
      pollTimerRef.current = window.setInterval(() => {
        void pollJobOnce(jobId);
      }, 1000);
    };
  }

  function setSubmittedJob(job: EngineJob) {
    setIsSubmittingRun(false);
    setState((current) => ({
      ...current,
      job,
      progress: {
        job_id: job.job_id,
        status: job.status,
        value: null,
        max: null,
        current_node: null,
        message: job.memory_status?.message ?? job.message ?? "Preparing workflow...",
      },
      result: null,
    }));
  }

  async function pollJobOnce(jobId: string) {
    const progress = await fetchJobProgress(jobId);
    setState((current) => ({ ...current, progress }));

    if (terminalStatuses.has(progress.status)) {
      setIsSubmittingRun(false);
      cleanupJobWatchers();
      const result = await fetchJobResult(jobId);
      if (isEngineJob(result)) {
        setSubmittedJob(result);
        if (isWatchableJob(result)) {
          watchJob(result.job_id);
          await pollJobOnce(result.job_id);
        }
        return;
      }
      setState((current) => ({ ...current, result }));
      if (result.status === "failed") {
        void openFailureDialog(result.error ?? progress.message ?? "The local engine could not finish this run.", jobId);
      }
    }
  }

  function cleanupJobWatchers() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  const unresolvedModelSummary = state.modelSummary?.models.filter((model) => model.status !== "available") ?? [];
  const missingModels = unresolvedModelSummary.length > 0 ? unresolvedModelSummary : state.validation?.missing_models ?? [];
  const workflowSummary = state.workflowStatus?.workflow;
  const trust = workflowSummary?.trust;
  const installStatus = typeof state.workflowStatus?.install?.status === "string"
    ? state.workflowStatus.install.status
    : null;
  const memoryStatus = state.result ? null : state.job?.memory_status ?? null;
  const backendKnownUnreachable = runtimeStatus.backendStatus === "unreachable";
  const engineKnownUnavailable =
    runtimeStatus.backendStatus === "reachable" &&
    (runtimeStatus.engineStatus === "offline" || runtimeStatus.engineStatus === "starting");
  const canRun = Boolean(
    state.workflowStatus?.can_prepare !== false
      && state.validation?.valid
      && state.modelSummary?.ready_to_run !== false
      && !backendKnownUnreachable
      && !engineKnownUnavailable
      && !isRunning
      && !isWaitingForMemory
      && !isBlockedByMemory,
  );
  const canCancel = Boolean(isRunning && state.job && !isBlockedByMemory);
  const progressPercent =
    state.progress?.value !== null && state.progress?.value !== undefined && state.progress.max
      ? Math.min(100, Math.round((state.progress.value / state.progress.max) * 100))
      : state.progress?.status === "completed"
        ? 100
        : 0;
  const topBarProgress = isRunning ? { percent: progressPercent } : null;

  const inputControls = allControls.filter(
    (c) => c.type !== "result_image" && c.type !== "display_image" && c.input_id,
  );

  const hasDashboard = Boolean(
    state.packageData?.dashboard?.status === "configured" && allControls.length > 0,
  );
  const showCanvasView = viewMode === "canvas" && (state.loading || hasDashboard);
  const isEditingLayout = draftLayoutOverrides !== null;

  function handleEditWidgets() {
    const schema = buildDashboardSchemaForEditing(
      workflowId,
      workflowSummary?.name ?? state.packageData?.metadata?.name ?? workflowId,
      allControls,
      inputIndex,
      outputIndex,
      layoutOverrides,
    );
    if (schema) onEditWidgets?.(schema);
  }

  function handleEnterEditLayout() {
    setDraftLayoutOverrides({ ...layoutOverrides });
  }

  async function handleSaveLayout() {
    if (!draftLayoutOverrides) return;
    const entries = Object.entries(draftLayoutOverrides);
    for (const [controlId, layout] of entries) {
      await setLayoutOverride(controlId, layout);
    }
    setDraftLayoutOverrides(null);
  }

  function handleCancelLayoutEdit() {
    setDraftLayoutOverrides(null);
  }

  const pageHeader = (
    <section className="page-heading page-heading--compact" aria-labelledby="workflow-title">
      <div>
        <button className="ghost-button ghost-button--back" type="button" onClick={onBack}>
          <ArrowLeft size={16} aria-hidden="true" />
          Back to Home
        </button>
        <div className="detail-eyebrow-row">
          <p className="eyebrow">{workflowSummary?.publisher_id ?? "Starter"} workflow</p>
          {trust ? (
            <span className={`trust-badge trust-badge--${trust.badge_tone}`} title={trust.summary}>
              {trust.label}
            </span>
          ) : null}
        </div>
        <h1 id="workflow-title">{workflowSummary?.name ?? state.packageData?.metadata?.name ?? "Workflow"}</h1>
        <p>
          {workflowSummary?.description
            ? workflowSummary.description.replace(/^Milestone \d+\s*/i, "")
            : "Describe the image you want, then let Noofy run the local workflow in the background."}
        </p>
      </div>
      <div className="button-row">
        <a
          className="secondary-button"
          href={exportWorkflowUrl(workflowId)}
          download
          aria-label="Share / Save as .noofy"
        >
          <Share2 size={15} aria-hidden="true" />
          Share
        </a>
        <button className="secondary-button" type="button" onClick={() => void loadRequirements()}>
          <RotateCcw size={16} aria-hidden="true" />
          Check Again
        </button>
      </div>
    </section>
  );

  const notices = (
    <>
      {state.error ? (
        <div className="notice notice--error" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The workflow is not ready</strong>
            <span>{state.error ?? "Start the backend and engine, then try again."}</span>
          </div>
        </div>
      ) : null}
      {runtimeStatus.backendStatus === "unreachable" ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The Noofy backend is offline</strong>
            <span>{runtimeStatus.refreshError ?? "Start the Noofy backend before running this workflow."}</span>
          </div>
        </div>
      ) : null}
      {runtimeStatus.backendStatus === "reachable" && runtimeStatus.engineStatus !== "ready" ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>{runtimeStatus.engineStatus === "starting" ? "The local AI engine is starting" : "The local AI engine is offline"}</strong>
            <span>Open Engine Settings to prepare or start the engine before running this workflow.</span>
          </div>
        </div>
      ) : null}
      {installStatus === "unsupported" ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>This workflow cannot run on this machine</strong>
            <span>You can still inspect and edit its dashboard.</span>
          </div>
        </div>
      ) : null}
      {missingModels.length > 0 ? (
        <div className="notice notice--warning" role="status">
          <Download size={18} aria-hidden="true" />
          <div>
            <strong>This workflow needs required models</strong>
            <span>
              {missingModels.map((model) => model.filename).join(", ")} must be available before this workflow can run.
            </span>
          </div>
        </div>
      ) : null}
      {memoryStatus ? (
        <div className={`notice ${memoryNoticeClass(memoryStatus)} notice--compact`} role="status">
          <AlertCircle size={16} aria-hidden="true" />
          <div>
            <strong>{memoryStatusTitle(memoryStatus.state)}</strong>
            <span>{memoryStatus.message}</span>
          </div>
        </div>
      ) : null}
    </>
  );

  const failureDialogElement = failureDialog ? (
    <WorkflowFailureDialog
      dialog={failureDialog}
      workflowId={workflowId}
      onClose={() => setFailureDialog(null)}
      onCopy={() => void handleCopyFailureLogs()}
    />
  ) : null;

  if (showCanvasView) {
    return (
      <AppLayout
        activeRoute="workflows"
        status={status}
        onNavigate={onNavigate}
        mainClassName="main-workspace--canvas-run"
        contentClassName="workspace-content--canvas-run"
        progress={topBarProgress}
      >
        <CanvasDashboardView
          controls={allControls}
          inputIndex={inputIndex}
          outputIndex={outputIndex}
          outputImagesByNodeId={outputImagesByNodeId}
          inputValues={inputValues}
          outputPreferences={outputPreferences}
          layoutOverrides={draftLayoutOverrides ?? layoutOverrides}
          isEditingLayout={isEditingLayout}
          runState={{
            isRunning,
            canRun,
            canCancel,
          }}
          exportNoofyUrl={exportWorkflowUrl(workflowId)}
          onChange={(inputId, value) => setInputValue(inputId, value)}
          onImageUpload={handleImageUpload}
          onOutputPreferenceChange={(controlId, autoSave) => setOutputPreference(controlId, { auto_save: autoSave })}
          onRun={() => void handleRun()}
          onCancel={() => void handleCancel()}
          onRestoreDefaults={() => void restoreDefaults()}
          onEnterEditLayout={handleEnterEditLayout}
          onSaveLayout={() => void handleSaveLayout()}
          onCancelLayoutEdit={handleCancelLayoutEdit}
          onEditWidgets={onEditWidgets ? handleEditWidgets : undefined}
          onLayoutOverride={(controlId: string, layout: GridItemLayout) =>
            setDraftLayoutOverrides((current) => ({ ...(current ?? layoutOverrides), [controlId]: layout }))
          }
        />
        {failureDialogElement}
      </AppLayout>
    );
  }

  return (
    <AppLayout activeRoute="workflows" status={status} onNavigate={onNavigate} progress={topBarProgress}>
      {pageHeader}
      {notices}

      <section className="run-workspace">
        <form className="run-panel" onSubmit={(event) => event.preventDefault()}>
          <div className="panel-heading">
            <div>
              <h2>Inputs</h2>
              <p>
                {hasDashboard
                  ? "Fill in the controls below, then click Run."
                  : "Keep it simple. Advanced workflow widgets can come later."}
              </p>
            </div>
          </div>

          {hasDashboard ? (
            <DashboardInputControls
              controls={inputControls}
              inputIndex={inputIndex}
              inputValues={inputValues}
              onChange={(id, value) => setInputValue(id, value)}
              onImageUpload={handleImageUpload}
            />
          ) : (
            <FallbackInputs
              inputValues={inputValues}
              inputs={state.packageData?.inputs ?? []}
              onChange={(id, value) => setInputValue(id, value)}
            />
          )}

          <div className="button-row">
            <button className="primary-button" type="button" disabled={!canRun} onClick={() => void handleRun()}>
              {isRunning ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <Play size={18} aria-hidden="true" />}
              Run Workflow
            </button>
            <button className="secondary-button" type="button" disabled={!canCancel} onClick={() => void handleCancel()}>
              <Square size={16} aria-hidden="true" />
              Cancel
            </button>
          </div>
        </form>

        <aside className="preview-panel">
          <div className="panel-heading">
            <div>
              <h2>Preview</h2>
              <p>{progressMessage(state.progress, state.result)}</p>
            </div>
            {state.validation?.valid ? (
              <span className="mini-status">
                <CheckCircle2 size={13} aria-hidden="true" />
                Ready
              </span>
            ) : null}
          </div>

          <div className="preview-stage">
            {outputImages[0] ? (
              <img src={outputImages[0]} alt="Generated workflow output" />
            ) : (
              <div className="preview-empty">
                <Image size={48} aria-hidden="true" />
                <span>Your generated image will appear here.</span>
              </div>
            )}
          </div>

          {state.result?.status === "failed" ? (
            <div className="notice notice--error notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Workflow failed</strong>
                <span>{state.result.error ?? "Open details for the technical error."}</span>
              </div>
            </div>
          ) : null}
        </aside>
      </section>
      {failureDialogElement}
    </AppLayout>
  );
}

function WorkflowFailureDialog({
  dialog,
  workflowId,
  onClose,
  onCopy,
}: {
  dialog: RunFailureDialogState;
  workflowId: string;
  onClose: () => void;
  onCopy: () => void;
}) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-failure-title">
      <section className="workflow-failure-modal">
        <header className="workflow-failure-modal__header">
          <div>
            <p className="eyebrow">Workflow run</p>
            <h2 id="workflow-failure-title">Workflow failed</h2>
            <p>{dialog.errorMessage}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="workflow-failure-modal__body">
          <div className="workflow-failure-modal__meta">
            <span>Workflow: {workflowId}</span>
            {dialog.jobId ? <span>Job: {dialog.jobId}</span> : <span>Job: not created yet</span>}
          </div>
          {dialog.logError ? (
            <div className="notice notice--warning notice--compact" role="status">
              <AlertCircle size={16} aria-hidden="true" />
              <div>
                <strong>Logs could not be loaded</strong>
                <span>{dialog.logError}</span>
              </div>
            </div>
          ) : null}
          <DiagnosticLogSection
            title="ComfyUI engine logs"
            events={dialog.comfyuiLogs}
            loading={dialog.logsLoading}
            emptyMessage="No ComfyUI engine logs were returned for this failure."
          />
          <DiagnosticLogSection
            title="Noofy logs"
            events={dialog.noofyLogs}
            loading={dialog.logsLoading}
            emptyMessage="No Noofy logs were returned for this failure."
          />
        </div>

        <footer className="workflow-failure-modal__footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <button className="primary-button" type="button" onClick={onCopy}>
            <Clipboard size={16} aria-hidden="true" />
            {dialog.copied ? "Copied" : "Copy logs"}
          </button>
        </footer>
      </section>
    </div>
  );
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

// ─── Schema-driven input controls ───────────────────────────────────────────

function DashboardInputControls({
  controls,
  inputIndex,
  inputValues,
  onChange,
  onImageUpload,
}: {
  controls: DashboardControlDef[];
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
}) {
  return (
    <>
      {controls.map((control) => {
        if (!control.input_id) return null;
        const input = inputIndex.get(control.input_id);
        if (!input) return null;
        const value = inputValues[input.id];

        return (
          <DashboardInputControl
            key={control.id}
            control={control}
            input={input}
            value={value}
            onChange={(v) => onChange(input.id, v)}
            onImageUpload={(file) => onImageUpload(input.id, file)}
          />
        );
      })}
    </>
  );
}

// ─── Fallback inputs (no configured dashboard) ──────────────────────────────

function FallbackInputs({
  inputs,
  inputValues,
  onChange,
}: {
  inputs: WorkflowInputDef[];
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
}) {
  if (inputs.length === 0) {
    return (
      <>
        <label className="field-group">
          <span>Prompt</span>
          <textarea
            value={typeof inputValues["prompt"] === "string" ? inputValues["prompt"] : ""}
            onChange={(e) => onChange("prompt", e.target.value)}
            rows={7}
          />
        </label>
        <div className="input-grid">
          <label className="field-group">
            <span>Variation ID</span>
            <input
              type="number"
              min={0}
              value={typeof inputValues["seed"] === "number" ? inputValues["seed"] : 5}
              onChange={(e) => onChange("seed", Number(e.target.value))}
            />
          </label>
          <label className="field-group">
            <span>Width</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["width"] === "number" ? inputValues["width"] : 512}
              onChange={(e) => onChange("width", Number(e.target.value))}
            />
            <small>{typeof inputValues["width"] === "number" ? inputValues["width"] : 512}px</small>
          </label>
          <label className="field-group">
            <span>Height</span>
            <input
              type="range"
              min={256}
              max={1024}
              step={64}
              value={typeof inputValues["height"] === "number" ? inputValues["height"] : 512}
              onChange={(e) => onChange("height", Number(e.target.value))}
            />
            <small>{typeof inputValues["height"] === "number" ? inputValues["height"] : 512}px</small>
          </label>
        </div>
      </>
    );
  }

  return (
    <>
      {inputs.map((input) => {
        const value = inputValues[input.id];
        return (
          <label key={input.id} className="field-group">
            <span>{input.label}</span>
            <input
              type="text"
              value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
              onChange={(e) => onChange(input.id, e.target.value)}
            />
          </label>
        );
      })}
    </>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function isWatchableJob(job: EngineJob) {
  return watchableJobStatuses.has(job.status);
}

function optimisticProgress(): JobProgress {
  return {
    job_id: optimisticJobId,
    status: "queued",
    value: 0,
    max: null,
    current_node: null,
    message: "Starting workflow...",
  };
}

function progressMessage(progress: JobProgress | null, result: JobResult | null) {
  if (result?.status === "completed") return "Result saved by the local workflow.";
  if (result?.status === "failed") return "The local engine could not finish this run.";
  if (progress?.status === "canceled") return "Run canceled.";
  if (progress?.status === "running") return progress.message ?? "Generating image...";
  if (progress?.status === "queued") return progress.message ?? "Preparing workflow...";
  if (progress?.status === "queued_pending_memory") return progress.message ?? "Waiting for memory.";
  if (progress?.status === "blocked_by_memory") return progress.message ?? "This workflow needs more memory.";
  return "Run the workflow to create your first result.";
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

function formatFailureReport(workflowId: string, dialog: RunFailureDialogState) {
  return [
    "Workflow failure report",
    `Workflow: ${workflowId}`,
    `Job: ${dialog.jobId ?? "not created yet"}`,
    `Error: ${dialog.errorMessage}`,
    "",
    "ComfyUI engine logs",
    formatDiagnosticEvents(dialog.comfyuiLogs) || "No ComfyUI engine logs were returned for this failure.",
    "",
    "Noofy logs",
    formatDiagnosticEvents(dialog.noofyLogs) || "No Noofy logs were returned for this failure.",
  ].join("\n");
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

function memoryStatusTitle(state: string) {
  if (state === "waiting_for_gpu") return "Waiting for the GPU";
  if (state === "freeing_memory" || state === "waiting_for_memory_release") return "Freeing memory";
  if (state === "retrying_after_memory_cleanup") return "Trying again";
  if (state === "blocked_by_memory" || state === "memory_cleanup_failed") return "Not enough memory";
  if (state === "ready_warm_co_resident" || state === "ready_reusing_runner") return "Ready to relaunch";
  return "Memory status";
}

function memoryNoticeClass(status: MemoryStatus) {
  if (status.state === "blocked_by_memory" || status.state === "memory_cleanup_failed") return "notice--error";
  return "notice--warning";
}

function extractImageUrls(result: JobResult | null) {
  if (!result) return [];
  const urls: string[] = [];
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object" || !("images" in outputPayload)) continue;
    const images = outputPayload.images;
    if (!Array.isArray(images)) continue;
    for (const image of images) {
      if (image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        urls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
      }
    }
  }
  return urls;
}

function buildDashboardSchemaForEditing(
  workflowId: string,
  workflowName: string,
  controls: DashboardControlDef[],
  inputIndex: Map<string, WorkflowInputDef>,
  outputIndex: Map<string, WorkflowOutputDef>,
  layoutOverrides: Record<string, GridItemLayout>,
): DashboardSchema | null {
  const widgets: DashboardWidget[] = [];

  for (const control of controls) {
    const override = layoutOverrides[control.id];
    const layout = layoutForBuilderControl(control, override);

    if (control.input_id) {
      const input = inputIndex.get(control.input_id);
      if (!input) continue;
      widgets.push({
        id: control.id,
        valueId: input.id,
        binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
        widgetType: toBuilderWidgetType(control.type),
        title: control.label,
        description: control.description ?? "",
        orientation: "vertical",
        group: toBuilderWidgetGroup(control.group),
        defaultValue: input.default,
        min: numberValidation(input.validation.min),
        max: numberValidation(input.validation.max),
        step: numberValidation(input.validation.step),
        options: stringArrayValidation(input.validation.options),
        layout,
      });
      continue;
    }

    if (control.output_id) {
      const output = outputIndex.get(control.output_id);
      if (!output) continue;
      widgets.push({
        id: control.id,
        valueId: output.id,
        binding: { nodeId: output.node_id, inputName: "" },
        widgetType: "display_image",
        title: control.label,
        description: control.description ?? "",
        orientation: "vertical",
        group: toBuilderWidgetGroup(control.group),
        defaultValue: null,
        showDownload: Boolean(control.show_download),
        layout,
      });
    }
  }

  if (widgets.length === 0) return null;

  return {
    version: 1,
    workflowId,
    workflowName,
    widgets,
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
  };
}

function layoutForBuilderControl(
  control: DashboardControlDef,
  override?: GridItemLayout,
): DashboardWidget["layout"] {
  const fallback = defaultLayoutForWidgetType(control.type);
  if (override) {
    return {
      x: override.x,
      y: override.y,
      w: override.w,
      h: override.h,
      minW: override.minW ?? control.layout?.min_w ?? fallback.minW,
      minH: override.minH ?? control.layout?.min_h ?? fallback.minH,
    };
  }

  if (!control.layout) return undefined;

  return {
    x: control.layout.x,
    y: control.layout.y,
    w: control.layout.w,
    h: control.layout.h,
    minW: control.layout.min_w ?? fallback.minW,
    minH: control.layout.min_h ?? fallback.minH,
  };
}

function toBuilderWidgetType(type: string): WidgetType {
  if (type === "result_image") return "display_image";
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "toggle",
    "load_image",
    "load_image_mask",
    "display_image",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : "string_field";
}

function toBuilderWidgetGroup(group: string | undefined): WidgetGroup {
  return group === "advanced" ? "advanced" : "simple";
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const options = value.filter((option): option is string => typeof option === "string" && option.length > 0);
  return options.length > 0 ? options : undefined;
}
