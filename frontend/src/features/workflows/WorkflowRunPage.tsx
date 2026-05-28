import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Clipboard,
  CheckCircle2,
  Download,
  FileJson,
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
  cancelModelDownload,
  createJobEventsUrl,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchModelDownloadStatus,
  fetchApiKeySettings,
  fetchJobLogs,
  fetchJobProgress,
  fetchJobResult,
  fetchLogs,
  fetchWorkflowModelVerificationStatus,
  fetchWorkflowModelSummary,
  fetchWorkflowPackage,
  fetchWorkflowStatus,
  isEngineJob,
  closeWorkflowRunnerLease,
  openWorkflowRunnerLease,
  resetDashboardCustomization,
  resolveBackendUrl,
  runWorkflow,
  saveDashboard,
  startWorkflowModelVerification,
  startModelDownload,
  uploadDashboardAsset,
  validateWorkflow,
  type DashboardSavePayload,
  type DashboardControlDef,
  type DashboardControlGroupDef,
  type DiagnosticEvent,
  type EngineJob,
  type ApiKeySettingsResponse,
  type JobProgress,
  type JobResult,
  type MemoryStatus,
  type ModelDownloadJobStatus,
  type ModelDownloadSelection,
  type RequiredModelAvailability,
  type RequiredModelSummary,
  type WorkflowInputDef,
  type WorkflowModelVerificationJobStatus,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
  type WorkflowStatusResponse,
  type WorkflowValidationResult,
} from "../../lib/api/noofyApi";
import type {
  DashboardSchema,
  DashboardWidget,
  WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type { GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetGroup, defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { useWorkflowUserState } from "../../lib/useWorkflowUserState";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { useOptionalWorkflowTabs, type WorkflowRuntimeHandleSource } from "../app/WorkflowTabs";
import { CanvasDashboardView, type CanvasActionBarPosition } from "./CanvasDashboardView";
import { CivitaiLoraBrowserModal } from "./CivitaiLoraBrowserModal";
import { ModelVerificationProgressPanel } from "./ModelVerificationProgressPanel";
import { WorkflowExportDialog } from "./WorkflowExportDialog";
import { DashboardInputControl, type LoraBrowserControlProps } from "./DashboardInputControl";
import { groupedControlIdSet, topLevelDashboardControlItems, type DashboardTopLevelControlItem } from "./dashboardTopLevelItems";
import type { WorkflowExportReviewModel } from "../../lib/workflowExport";

interface WorkflowRunPageProps {
  workflowId: string;
  onBack: () => void;
  onWorkflowNameChange?: (workflowName: string) => void;
  onEditWidgets?: (schema: DashboardSchema) => void;
  onNavigate: (route: AppRouteId) => void;
}

interface RunPageState {
  loading: boolean;
  workflowStatus: WorkflowStatusResponse | null;
  modelSummary: RequiredModelSummary | null;
  packageData: WorkflowPackageResponse | null;
  apiKeySettings: ApiKeySettingsResponse | null;
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

type PreparationPhaseStatus = "pending" | "active" | "passed" | "failed" | "blocked";

interface PreparationPhase {
  id: string;
  label: string;
  status: PreparationPhaseStatus;
}

interface RunPreparationDialogState {
  message: string;
  detail: string | null;
  phases: PreparationPhase[];
}

interface LoraBrowserDialogState {
  control: DashboardControlDef;
  input: WorkflowInputDef;
}

const initialState: RunPageState = {
  loading: true,
  workflowStatus: null,
  modelSummary: null,
  packageData: null,
  apiKeySettings: null,
  validation: null,
  job: null,
  progress: null,
  result: null,
  error: null,
};

const terminalStatuses = new Set(["completed", "failed", "canceled"]);
const watchableJobStatuses = new Set(["queued", "running"]);
const preparationFailureStatuses = new Set([
  "blocked_by_policy",
  "cannot_prepare_automatically",
  "failed",
  "prepared_needs_input_setup",
  "unsupported",
  "unsupported_runtime_profile",
]);
const preparationBlockedStatuses = new Set([
  "blocked_by_policy",
  "prepared_needs_input_setup",
  "unsupported",
  "unsupported_runtime_profile",
]);
const optimisticJobId = "__pending_workflow_run__";
const logLimit = 200;

export function WorkflowRunPage({ workflowId, onBack, onWorkflowNameChange, onEditWidgets, onNavigate }: WorkflowRunPageProps) {
  const [state, setState] = useState<RunPageState>(initialState);
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [failureDialog, setFailureDialog] = useState<RunFailureDialogState | null>(null);
  const [runPreparationDialog, setRunPreparationDialog] = useState<RunPreparationDialogState | null>(null);
  const [loraBrowserDialog, setLoraBrowserDialog] = useState<LoraBrowserDialogState | null>(null);
  const [exportDialog, setExportDialog] = useState<{ extension: ".noofy" | ".json"; url: string } | null>(null);
  const [requiredModelsModalOpen, setRequiredModelsModalOpen] = useState(false);
  const [modelDownloadJob, setModelDownloadJob] = useState<ModelDownloadJobStatus | null>(null);
  const [modelDownloadError, setModelDownloadError] = useState<string | null>(null);
  const [modelDownloadStarting, setModelDownloadStarting] = useState(false);
  const [modelVerificationJob, setModelVerificationJob] = useState<WorkflowModelVerificationJobStatus | null>(null);
  const [modelVerificationError, setModelVerificationError] = useState<string | null>(null);
  const [downloadedLoraOptions, setDownloadedLoraOptions] = useState<Record<string, string[]>>({});
  const [draftLayoutOverrides, setDraftLayoutOverrides] = useState<Record<string, GridItemLayout> | null>(null);
  const [draftActionBarPosition, setDraftActionBarPosition] = useState<CanvasActionBarPosition | null>(null);
  const [draftActionBarTouched, setDraftActionBarTouched] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const modelVerificationStartInFlightRef = useRef(false);

  const { viewMode } = useAppPreferences();
  const runtimeStatus = useRuntimeStatus();
  const workflowTabs = useOptionalWorkflowTabs();
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
      defaults[input.id] = defaultValueForWorkflowInput(input);
    }
    return defaults;
  }, [state.packageData]);

  const allControls = useMemo(
    () => state.packageData?.dashboard?.sections.flatMap((section) => section.controls) ?? [],
    [state.packageData],
  );
  const allGroups = useMemo(
    () => state.packageData?.dashboard?.sections.flatMap((section) => section.groups ?? []) ?? [],
    [state.packageData],
  );
  const topLevelItems = useMemo(
    () =>
      state.packageData?.dashboard?.sections.flatMap((section) =>
        topLevelDashboardControlItems(section.controls, section.groups ?? []),
      ) ?? [],
    [state.packageData],
  );
  const dashboardVersion = useMemo(
    () => dashboardUserStateVersion(state.packageData),
    [state.packageData],
  );
  const dashboardControlIds = useMemo(() => allControls.map((control) => control.id), [allControls]);
  const dashboardLayoutIds = useMemo(() => topLevelItems.map((item) => item.id), [topLevelItems]);

  const {
    values: inputValues,
    setValue: setInputValue,
    restoreDefaults,
    layoutOverrides,
    setLayoutOverride,
    resetLayout,
    outputPreferences,
    setOutputPreference,
    getOutputPreferencesSnapshot,
    actionBarPositionOverride,
    setActionBarPositionOverride,
  } = useWorkflowUserState(workflowId, packageDefaults, dashboardVersion, inputIndex, dashboardLayoutIds, dashboardControlIds);

  const submittedInputValues = useMemo(
    () => normalizedLoraInputValues(state.packageData, inputValues),
    [state.packageData, inputValues],
  );
  const activeModelSummary = useMemo(
    () => activeRequiredModelSummary(state.modelSummary, state.packageData, submittedInputValues),
    [state.modelSummary, state.packageData, submittedInputValues],
  );
  const activeValidation = useMemo(
    () => activeWorkflowValidation(state.validation, state.packageData, submittedInputValues),
    [state.validation, state.packageData, submittedInputValues],
  );

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
      const [workflowStatus, packageData, modelSummary, apiKeySettings] = await Promise.all([
        fetchWorkflowStatus(workflowId).catch(() => null),
        fetchWorkflowPackage(workflowId).catch(() => null),
        fetchWorkflowModelSummary(workflowId).catch(() => null),
        fetchApiKeySettings().catch(() => null),
      ]);

      const validation = await validateWorkflow(workflowId);
      setState((current) => ({ ...current, loading: false, workflowStatus, modelSummary, packageData, apiKeySettings, validation }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        workflowStatus: null,
        modelSummary: null,
        packageData: null,
        apiKeySettings: null,
        validation: null,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  useEffect(() => {
    void runtimeStatus.refreshRuntime({ silent: true });
    void loadRequirements();
    setRequiredModelsModalOpen(false);
    setModelDownloadJob(null);
    setModelDownloadError(null);
    setModelDownloadStarting(false);
    setModelVerificationJob(null);
    setModelVerificationError(null);
    return () => {
      cleanupJobWatchers();
    };
  }, [workflowId, runtimeStatus.refreshRuntime]);

  useEffect(() => {
    if (!workflowTabs) return;
    let canceled = false;
    openWorkflowRunnerLease(workflowId)
      .then((response) => {
        if (!response.lease_id) return;
        if (canceled) {
          void closeWorkflowRunnerLease(workflowId, response.lease_id);
          return;
        }
        workflowTabs.setWorkflowRuntime(workflowId, {
          runnerLeaseId: response.lease_id,
          runnerId: runnerIdFromLease(response.runner),
        });
      })
      .catch(() => {
        // A workflow can be opened without a bound isolated runner; tabs remain navigation-only.
      });
    return () => {
      canceled = true;
    };
  }, [workflowId]);

  useEffect(() => {
    if (!modelDownloadJob || !["queued", "running"].includes(modelDownloadJob.status)) return;
    const interval = window.setInterval(() => {
      fetchModelDownloadStatus(modelDownloadJob.job_id)
        .then((job) => {
          setModelDownloadJob(job);
          setModelDownloadError(null);
          if (!["queued", "running"].includes(job.status)) {
            void loadRequirements();
          }
        })
        .catch((error) => {
          setModelDownloadError(error instanceof Error ? error.message : "Could not check model download progress.");
        });
    }, 700);
    return () => window.clearInterval(interval);
  }, [modelDownloadJob?.job_id, modelDownloadJob?.status]);

  useEffect(() => {
    if (!requiredModelsModalOpen || !hasVerifiableLocalModels(activeModelSummary)) return;
    if (modelVerificationJob || modelVerificationError) return;
    let canceled = false;
    void startLocalModelVerification(() => canceled);
    return () => {
      canceled = true;
    };
  }, [requiredModelsModalOpen, activeModelSummary, workflowId, modelVerificationJob?.status, modelVerificationError]);

  useEffect(() => {
    if (!modelVerificationJob || !["queued", "running"].includes(modelVerificationJob.status)) return;
    const interval = window.setInterval(() => {
      fetchWorkflowModelVerificationStatus(workflowId, modelVerificationJob.job_id)
        .then((job) => {
          setModelVerificationJob(job);
          setModelVerificationError(null);
          if (!["queued", "running"].includes(job.status)) {
            if (job.model_summary) {
              setState((current) => ({ ...current, modelSummary: job.model_summary }));
            }
            void loadRequirements();
          }
        })
        .catch((error) => {
          setModelVerificationError(error instanceof Error ? error.message : "Could not check model verification progress.");
        });
    }, 800);
    return () => window.clearInterval(interval);
  }, [modelVerificationJob?.job_id, modelVerificationJob?.status, workflowId]);

  function startRunPreparationStatusPolling() {
    let stopped = false;
    const poll = async () => {
      try {
        const statusResponse = await fetchWorkflowStatus(workflowId);
        if (stopped) return;
        setState((current) => ({ ...current, workflowStatus: statusResponse }));
        setRunPreparationDialog(runPreparationDialogFromStatus(statusResponse));
      } catch {
        // Keep the in-progress dialog visible; the run request will surface the real failure.
      }
    };
    void poll();
    const interval = window.setInterval(() => void poll(), 900);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }

  async function handleRun() {
    if (!canRun) {
      return;
    }

    const shouldTrackPreparation = shouldShowRunPreparationDialog(state.workflowStatus);
    let stopPreparationPolling: (() => void) | null = null;
    cleanupJobWatchers();
    setIsSubmittingRun(true);
    setFailureDialog(null);
    if (shouldTrackPreparation) {
      setRunPreparationDialog(runPreparationDialogFromStatus(state.workflowStatus));
      stopPreparationPolling = startRunPreparationStatusPolling();
    } else {
      setRunPreparationDialog(null);
    }
    setState((current) => ({
      ...current,
      job: null,
      progress: optimisticProgress(),
      result: null,
      error: null,
    }));

    try {
      const response = await runWorkflow(workflowId, {
        inputs: submittedInputValues,
        options: {},
        output_preferences_snapshot: getOutputPreferencesSnapshot(),
      });

      if (!isEngineJob(response)) {
        stopPreparationPolling?.();
        setIsSubmittingRun(false);
        setRunPreparationDialog(null);
        const message = workflowValidationErrorMessage(response);
        setState((current) => ({
          ...current,
          validation: response,
          progress: null,
          error: response.valid ? null : message,
        }));
        if (!response.valid) {
          void openFailureDialog(message, null);
        }
        return;
      }

      stopPreparationPolling?.();
      setIsSubmittingRun(false);
      setRunPreparationDialog(null);
      setSubmittedJob(response);
      if (isWatchableJob(response)) {
        watchJob(response.job_id);
        await pollJobOnce(response.job_id);
      }
    } catch (error) {
      stopPreparationPolling?.();
      const message = error instanceof Error ? error.message : String(error);
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      setIsSubmittingRun(false);
      setRunPreparationDialog(null);
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
      recordWorkflowProgress(progress);
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleDownloadRequiredModels() {
    const selections = requiredModelDownloadSelections(activeModelSummary, workflowId);
    if (selections.length === 0) return;
    setModelDownloadStarting(true);
    setModelDownloadError(null);
    try {
      const started = await startModelDownload(selections);
      const status = await fetchModelDownloadStatus(started.job_id);
      setModelDownloadJob(status);
    } catch (error) {
      setModelDownloadError(error instanceof Error ? error.message : "Could not start the model download.");
    } finally {
      setModelDownloadStarting(false);
    }
  }

  async function handleCancelModelDownload() {
    if (!modelDownloadJob) return;
    try {
      setModelDownloadJob(await cancelModelDownload(modelDownloadJob.job_id));
    } catch (error) {
      setModelDownloadError(error instanceof Error ? error.message : "Could not cancel the model download.");
    }
  }

  async function startLocalModelVerification(isCanceled: () => boolean = () => false) {
    if (modelVerificationStartInFlightRef.current) return;
    modelVerificationStartInFlightRef.current = true;
    setModelVerificationJob(null);
    setModelVerificationError(null);
    try {
      const job = await startWorkflowModelVerification(workflowId);
      if (!isCanceled()) setModelVerificationJob(job);
    } catch (error) {
      if (!isCanceled()) {
        setModelVerificationError(error instanceof Error ? error.message : "Could not start local model verification.");
      }
    } finally {
      modelVerificationStartInFlightRef.current = false;
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

  function loraBrowserFor(control: DashboardControlDef, input: WorkflowInputDef) {
    if (control.type !== "lora_loader") return undefined;
    const civitaiConfigured = Boolean(state.apiKeySettings?.providers?.civitai?.configured);
    const disabledReason = !civitaiConfigured
      ? "Requires a CivitAI API key. Add one in Settings to search and download LoRAs."
      : !state.packageData
        ? "Workflow context is still loading."
        : undefined;
    return {
      enabled: civitaiConfigured && Boolean(state.packageData),
      disabledReason,
      extraOptions: downloadedLoraOptions[input.id] ?? [],
      onOpen: () => setLoraBrowserDialog({ control, input }),
    };
  }

  function handleLoraDownloadCompleted(inputId: string, targetFilename: string, observedValue: string | null) {
    setDownloadedLoraOptions((current) => ({
      ...current,
      [inputId]: Array.from(new Set([...(current[inputId] ?? []), targetFilename])),
    }));
    void loadRequirements();
    const currentValue = submittedInputValues[inputId];
    const currentString = typeof currentValue === "string" ? currentValue : currentValue == null ? null : String(currentValue);
    if (currentString === observedValue) {
      setInputValue(inputId, targetFilename);
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
      recordWorkflowProgress(progress);
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
      recordWorkflowTerminalResult(result);
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
    recordWorkflowJob(job);
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
    try {
      const progress = await fetchJobProgress(jobId);
      setState((current) => ({ ...current, progress, error: null }));
      recordWorkflowProgress(progress);

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
        recordWorkflowTerminalResult(result);
        if (result.status === "failed") {
          void openFailureDialog(result.error ?? progress.message ?? "The local engine could not finish this run.", jobId);
        }
      }
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Could not check workflow progress.",
      }));
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

  function recordWorkflowJob(job: EngineJob) {
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: job.job_id,
      activeJobStatus: job.status,
      handleSource: workflowHandleSource(job),
      queueId: job.queue_id ?? (job.status === "queued_pending_memory" ? job.job_id : null),
    });
  }

  function recordWorkflowProgress(progress: JobProgress) {
    if (terminalStatuses.has(progress.status)) {
      workflowTabs?.setWorkflowRuntime(workflowId, {
        activeJobId: null,
        activeJobStatus: progress.status,
        handleSource: null,
        queueId: null,
      });
      return;
    }
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: progress.job_id,
      activeJobStatus: progress.status,
    });
  }

  function recordWorkflowTerminalResult(result: JobResult) {
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: null,
      activeJobStatus: result.status,
      handleSource: null,
      queueId: null,
    });
  }

  const unresolvedModelSummary = activeModelSummary?.models.filter((model) => model.status !== "available") ?? [];
  const missingModels = unresolvedModelSummary.length > 0 ? unresolvedModelSummary : activeValidation?.missing_models ?? [];
  const workflowSummary = state.workflowStatus?.workflow;
  const trust = workflowSummary?.trust;

  useEffect(() => {
    const name = workflowSummary?.name ?? state.packageData?.metadata?.name;
    if (name) onWorkflowNameChange?.(name);
  }, [state.packageData?.metadata?.name, workflowSummary?.name]);

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
      && activeValidation?.valid
      && activeModelSummary?.ready_to_run !== false
      && !backendKnownUnreachable
      && !engineKnownUnavailable
      && !isRunning
      && !isWaitingForMemory
      && !isBlockedByMemory,
  );
  const hasDownloadableRequiredModels = requiredModelDownloadSelections(activeModelSummary, workflowId).length > 0;
  const hasRequiredModelFixAction = Boolean(
    activeModelSummary && (missingModels.length > 0 || activeModelSummary.ready_to_run === false),
  );
  const runDisabledReason = canRun
    ? null
    : workflowRunDisabledReason({
        backendKnownUnreachable,
        engineKnownUnavailable,
        installStatus,
        isBlockedByMemory,
        isRunning,
        isWaitingForMemory,
        loading: state.loading,
        missingModels,
        modelSummaryReady: activeModelSummary?.ready_to_run,
        validation: activeValidation,
        workflowStatus: state.workflowStatus,
      });
  const canCancel = Boolean(isRunning && state.job && !isBlockedByMemory);
  const progressPercent =
    state.progress?.value !== null && state.progress?.value !== undefined && state.progress.max
      ? Math.min(100, Math.round((state.progress.value / state.progress.max) * 100))
      : state.progress?.status === "completed"
        ? 100
        : 0;
  const topBarProgress = isRunning ? { percent: progressPercent } : null;

  const inputControls = allControls.filter(
    (c) => c.type === "api_credential" || (c.type !== "result_image" && c.type !== "display_image" && c.input_id),
  );
  const inputControlIds = useMemo(() => new Set(inputControls.map((control) => control.id)), [inputControls]);
  const inputTopLevelItems = useMemo(
    () =>
      topLevelItems
        .map((item) => {
          if (item.kind === "control") return inputControlIds.has(item.control.id) ? item : null;
          const controls = item.controls.filter((control) => inputControlIds.has(control.id));
          return controls.length > 0 ? { ...item, controls } : null;
        })
        .filter((item): item is NonNullable<typeof item> => Boolean(item)),
    [topLevelItems, inputControlIds],
  );

  const hasDashboard = Boolean(
    state.packageData?.dashboard?.status === "configured" && allControls.length > 0,
  );
  const showCanvasView = viewMode === "canvas" && (state.loading || hasDashboard);
  const isEditingLayout = draftLayoutOverrides !== null;
  const creatorActionBarPosition = actionBarPositionFromDashboard(
    state.packageData?.dashboard?.presentation?.action_bar,
  );
  const userActionBarPosition = actionBarPositionFromDashboard(actionBarPositionOverride);
  const canvasActionBarPosition = draftActionBarTouched
    ? draftActionBarPosition
    : userActionBarPosition ??
      (isEditingLayout ? draftActionBarPosition ?? creatorActionBarPosition : creatorActionBarPosition);

  function handleEditWidgets() {
    const schema = buildDashboardSchemaForEditing(
      workflowId,
      workflowSummary?.name ?? state.packageData?.metadata?.name ?? workflowId,
      allControls,
      allGroups,
      inputIndex,
      outputIndex,
      layoutOverrides,
      creatorActionBarPosition,
    );
    if (schema) onEditWidgets?.(schema);
  }

  function handleEnterEditLayout() {
    setDraftLayoutOverrides({ ...layoutOverrides });
    setDraftActionBarPosition(creatorActionBarPosition);
    setDraftActionBarTouched(false);
  }

  async function handleSaveLayout() {
    if (!draftLayoutOverrides) return;
    try {
      const entries = Object.entries(draftLayoutOverrides);
      for (const [controlId, layout] of entries) {
        await setLayoutOverride(controlId, layout);
      }
      if (draftActionBarTouched && draftActionBarPosition && state.packageData) {
        await saveDashboard(
          workflowId,
          dashboardSavePayloadWithActionBarPosition(state.packageData, draftActionBarPosition),
        );
        await setActionBarPositionOverride(draftActionBarPosition);
        setState((current) => updatePackageActionBarPosition(current, draftActionBarPosition));
      }
      setDraftLayoutOverrides(null);
      setDraftActionBarPosition(null);
      setDraftActionBarTouched(false);
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  function handleCancelLayoutEdit() {
    setDraftLayoutOverrides(null);
    setDraftActionBarPosition(null);
    setDraftActionBarTouched(false);
  }

  async function handleRestoreDefaults() {
    try {
      await restoreDefaults();
      await resetLayout();
      await resetDashboardCustomization(workflowId);
      await loadRequirements();
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
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
        <button
          className="secondary-button"
          type="button"
          aria-label="Share / Save as .noofy"
          onClick={() => setExportDialog({ extension: ".noofy", url: exportWorkflowUrl(workflowId) })}
        >
          <Share2 size={15} aria-hidden="true" />
          Share
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() => setExportDialog({ extension: ".json", url: exportWorkflowComfyJsonUrl(workflowId) })}
        >
          <FileJson size={15} aria-hidden="true" />
          Export JSON
        </button>
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
            <span>{state.error ?? "Restart Noofy, then try again."}</span>
          </div>
        </div>
      ) : null}
      {runtimeStatus.backendStatus === "unreachable" ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The local app service is offline</strong>
            <span>{runtimeStatus.refreshError ?? "Restart Noofy before running this workflow."}</span>
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
          {hasRequiredModelFixAction ? (
            <button className="secondary-button secondary-button--small" type="button" onClick={() => setRequiredModelsModalOpen(true)}>
              <Download size={13} aria-hidden="true" />
              Download
            </button>
          ) : null}
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
  const preparationDialogElement = runPreparationDialog ? (
    <RunPreparationDialog dialog={runPreparationDialog} />
  ) : null;
  const loraBrowserElement = loraBrowserDialog ? (
    <CivitaiLoraBrowserModal
      workflowId={workflowId}
      control={loraBrowserDialog.control}
      input={loraBrowserDialog.input}
      inputValues={submittedInputValues}
      currentValue={submittedInputValues[loraBrowserDialog.input.id]}
      onClose={() => setLoraBrowserDialog(null)}
      onDownloadCompleted={(targetFilename, observedValue) =>
        handleLoraDownloadCompleted(loraBrowserDialog.input.id, targetFilename, observedValue)
      }
    />
  ) : null;
  const workflowDisplayName = workflowSummary?.name ?? state.packageData?.metadata?.name ?? "Workflow";
  const exportReview: WorkflowExportReviewModel = {
    name: workflowDisplayName,
    description: state.packageData?.metadata?.description ?? workflowSummary?.description ?? "",
    source: workflowSummary?.source_label ?? workflowSummary?.trust?.label ?? "Noofy workflow",
    requiredModels: activeModelSummary?.models?.map((model) => ({
      name: model.filename,
      type: model.model_type,
      status_label: model.status_label,
      folder: model.folder,
      size_bytes: model.size_bytes,
    })) ?? [],
  };
  const exportDialogElement = exportDialog ? (
    <WorkflowExportDialog
      workflowName={workflowDisplayName}
      exportUrl={exportDialog.url}
      extension={exportDialog.extension}
      inputValues={exportDialog.extension === ".json" ? submittedInputValues : undefined}
      review={exportDialog.extension === ".noofy" ? exportReview : undefined}
      onClose={() => setExportDialog(null)}
    />
  ) : null;
  const requiredModelsModalElement = requiredModelsModalOpen && activeModelSummary ? (
    <WorkflowRequiredModelsModal
      workflowName={workflowDisplayName}
      summary={activeModelSummary}
      downloadJob={modelDownloadJob}
      downloadError={modelDownloadError}
      downloadBusy={modelDownloadStarting}
      verificationJob={modelVerificationJob}
      verificationError={modelVerificationError}
      onDownload={() => void handleDownloadRequiredModels()}
      onCancelDownload={() => void handleCancelModelDownload()}
      onRetryVerification={() => void startLocalModelVerification()}
      onClose={() => setRequiredModelsModalOpen(false)}
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
          groups={allGroups}
          inputIndex={inputIndex}
          outputIndex={outputIndex}
          outputImagesByNodeId={outputImagesByNodeId}
          inputValues={inputValues}
          outputPreferences={outputPreferences}
          layoutOverrides={draftLayoutOverrides ?? layoutOverrides}
          actionBarPosition={canvasActionBarPosition}
          isEditingLayout={isEditingLayout}
          runState={{
            isRunning,
            canRun,
            canCancel,
            disabledReason: runDisabledReason,
            disabledActionLabel: hasRequiredModelFixAction ? "Download" : null,
          }}
          exportNoofyUrl={exportWorkflowUrl(workflowId)}
          exportComfyJsonUrl={exportWorkflowComfyJsonUrl(workflowId)}
          exportWorkflowName={workflowSummary?.name ?? state.packageData?.metadata?.name}
          exportReview={exportReview}
          onChange={(inputId, value) => setInputValue(inputId, value)}
          onImageUpload={handleImageUpload}
          loraBrowserFor={loraBrowserFor}
          onOutputPreferenceChange={(controlId, autoSave) => setOutputPreference(controlId, { auto_save: autoSave })}
          onRun={() => void handleRun()}
          onCancel={() => void handleCancel()}
          onDisabledRunAction={hasRequiredModelFixAction ? () => setRequiredModelsModalOpen(true) : undefined}
          onRestoreDefaults={() => void handleRestoreDefaults()}
          onEnterEditLayout={handleEnterEditLayout}
          onSaveLayout={() => void handleSaveLayout()}
          onCancelLayoutEdit={handleCancelLayoutEdit}
          onEditWidgets={onEditWidgets ? handleEditWidgets : undefined}
          onLayoutOverride={(controlId: string, layout: GridItemLayout) =>
            setDraftLayoutOverrides((current) => ({ ...(current ?? layoutOverrides), [controlId]: layout }))
          }
          onActionBarPositionChange={(position) => {
            if (isEditingLayout) {
              setDraftActionBarPosition(position);
              setDraftActionBarTouched(true);
              return;
            }
            void setActionBarPositionOverride(position);
          }}
        />
        {failureDialogElement}
        {preparationDialogElement}
        {loraBrowserElement}
        {exportDialogElement}
        {requiredModelsModalElement}
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
              items={inputTopLevelItems}
              inputIndex={inputIndex}
              inputValues={inputValues}
              onChange={(id, value) => setInputValue(id, value)}
              onImageUpload={handleImageUpload}
              loraBrowserFor={loraBrowserFor}
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
      {preparationDialogElement}
      {loraBrowserElement}
      {exportDialogElement}
      {requiredModelsModalElement}
    </AppLayout>
  );
}

function RunPreparationDialog({ dialog }: { dialog: RunPreparationDialogState }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-preparation-title">
      <section className="workflow-preparation-modal">
        <header className="workflow-preparation-modal__header">
          <div>
            <p className="eyebrow">Workflow run</p>
            <h2 id="workflow-preparation-title">Preparing workflow</h2>
            <p>{dialog.message}</p>
          </div>
          <Loader2 className="spin" size={22} aria-hidden="true" />
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
      </section>
    </div>
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
  items,
  inputIndex,
  inputValues,
  onChange,
  onImageUpload,
  loraBrowserFor,
}: {
  items: DashboardTopLevelControlItem[];
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
  return (
    <>
      {items.map((item) => {
        if (item.kind === "group") {
          return (
            <section className="field-group dashboard-control-group" key={item.id}>
              <span>{item.group.title}</span>
              {item.group.description ? <small>{item.group.description}</small> : null}
              <div className="dashboard-control-group__controls">
                {item.controls.map((control) => (
                  <ClassicDashboardInputControl
                    key={control.id}
                    control={control}
                    inputIndex={inputIndex}
                    inputValues={inputValues}
                    grouped
                    onChange={onChange}
                    onImageUpload={onImageUpload}
                    loraBrowserFor={loraBrowserFor}
                  />
                ))}
              </div>
            </section>
          );
        }
        return (
          <ClassicDashboardInputControl
            key={item.id}
            control={item.control}
            inputIndex={inputIndex}
            inputValues={inputValues}
            onChange={onChange}
            onImageUpload={onImageUpload}
            loraBrowserFor={loraBrowserFor}
          />
        );
      })}
    </>
  );
}

function ClassicDashboardInputControl({
  control,
  inputIndex,
  inputValues,
  grouped = false,
  onChange,
  onImageUpload,
  loraBrowserFor,
}: {
  control: DashboardControlDef;
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  grouped?: boolean;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
        const inputId = control.input_id ?? control.id;
        const input = control.type === "api_credential"
          ? credentialInputForControl(control)
          : inputIndex.get(inputId);
        if (!input) return null;
        const value = inputValues[input.id];

        return (
          <DashboardInputControl
            control={control}
            input={input}
            value={value}
            hideLabel={grouped}
            loraBrowser={loraBrowserFor?.(control, input)}
            onChange={(v) => onChange(input.id, v)}
            onImageUpload={(file) => onImageUpload(input.id, file)}
          />
        );
}

function credentialInputForControl(control: DashboardControlDef): WorkflowInputDef {
  return {
    id: control.input_id ?? control.id,
    label: control.label || "ComfyUI Account API Key",
    control: "api_credential",
    binding: { node_id: "", input_name: "" },
    default: {
      kind: "api_key_ref",
      provider: control.provider ?? "comfy_org",
      secret_ref: control.secret_ref ?? "api-key:comfy_org",
    },
    validation: {},
  };
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

function shouldShowRunPreparationDialog(workflowStatus: WorkflowStatusResponse | null) {
  const installStatus = workflowInstallStatus(workflowStatus);
  return Boolean(installStatus && installStatus !== "ready");
}

function runPreparationDialogFromStatus(workflowStatus: WorkflowStatusResponse | null): RunPreparationDialogState {
  const install = workflowStatus?.install ?? {};
  const installStatus = workflowInstallStatus(workflowStatus);
  const lastError = installString(install, "last_error");
  const userMessage = installString(install, "user_facing_message");
  const failed = Boolean(installStatus && preparationFailureStatuses.has(installStatus));
  return {
    message: failed
      ? lastError ?? userMessage ?? "Noofy could not prepare this workflow automatically."
      : userMessage ?? "Preparing the isolated runner for this workflow.",
    detail: failed ? userMessage && userMessage !== lastError ? userMessage : null : preparationStatusDetail(installStatus),
    phases: preparationPhases(workflowStatus),
  };
}

function workflowValidationErrorMessage(validation: WorkflowValidationResult) {
  const errors = validation.errors.map((error) => error.trim()).filter(Boolean);
  if (errors.length > 0) return errors.join("\n");
  if (validation.missing_models.length > 0) return "This workflow still needs required models before it can run.";
  return "Noofy could not start this workflow.";
}

function workflowInstallStatus(workflowStatus: WorkflowStatusResponse | null) {
  return installString(workflowStatus?.install ?? {}, "status");
}

function installString(install: Record<string, unknown>, key: string) {
  const value = install[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function preparationStatusDetail(status: string | null) {
  switch (status) {
    case "resolving_models":
    case "downloading":
    case "materializing_model_view":
      return "Checking required model files and staging the model view.";
    case "resolving_dependencies":
    case "materializing_dependencies":
    case "preparing_dependency_env":
    case "resolving_runtime_profile":
      return "Resolving Python packages into the workflow capsule.";
    case "materializing_custom_nodes":
    case "checking_compatibility":
      return "Staging custom nodes in the isolated runner workspace.";
    case "smoke_testing":
      return "Starting the isolated runner and checking custom-node registration.";
    default:
      return "Noofy will continue the run automatically when preparation is ready.";
  }
}

function preparationPhases(workflowStatus: WorkflowStatusResponse | null): PreparationPhase[] {
  const install = workflowStatus?.install ?? {};
  const installStatus = workflowInstallStatus(workflowStatus);
  const activePhase = activePreparationPhase(installStatus);
  const failed = Boolean(installStatus && preparationFailureStatuses.has(installStatus));
  const blocked = Boolean(installStatus && preparationBlockedStatuses.has(installStatus));
  const ready = installStatus === "ready";
  const phases: PreparationPhase[] = [
    { id: "models", label: "Check required models", status: "pending" },
    { id: "dependencies", label: "Prepare dependency environment", status: "pending" },
    { id: "stage_custom_nodes", label: "Stage custom-node files", status: "pending" },
    { id: "runner", label: "Start isolated runner", status: "pending" },
    { id: "custom_registration", label: "Check custom-node registration", status: "pending" },
    { id: "resume", label: "Continue run", status: "pending" },
  ];
  const activeIndex = activePhase ? phases.findIndex((phase) => phase.id === activePhase) : -1;

  return phases.map((phase, index) => {
    const smokeStage = smokeStageStatus(install, phase.id);
    if (smokeStage === "passed") return { ...phase, status: "passed" };
    if (smokeStage === "failed" || smokeStage === "blocked") return { ...phase, status: smokeStage };
    if (ready) return { ...phase, status: phase.id === "resume" ? "active" : "passed" };
    if (phase.id === activePhase) return { ...phase, status: failed ? (blocked ? "blocked" : "failed") : "active" };
    if (activeIndex > 0 && index < activeIndex) return { ...phase, status: "passed" };
    return phase;
  });
}

function activePreparationPhase(status: string | null) {
  switch (status) {
    case "resolving_models":
    case "downloading":
    case "materializing_model_view":
      return "models";
    case "resolving_dependencies":
    case "materializing_dependencies":
    case "preparing_dependency_env":
    case "resolving_runtime_profile":
    case "preparing":
    case "pending":
    case "imported":
      return "dependencies";
    case "materializing_custom_nodes":
    case "checking_compatibility":
      return "stage_custom_nodes";
    case "smoke_testing":
    case "prepared":
    case "starting":
      return "runner";
    case "ready":
      return "resume";
    case "failed":
    case "cannot_prepare_automatically":
    case "blocked_by_policy":
    case "unsupported_runtime_profile":
    case "unsupported":
      return "runner";
    case "prepared_needs_input_setup":
      return "resume";
    default:
      return "dependencies";
  }
}

function smokeStageStatus(install: Record<string, unknown>, phaseId: string): PreparationPhaseStatus | null {
  const stageName =
    phaseId === "dependencies"
      ? "dependency_env"
      : phaseId === "custom_registration"
        ? "custom_node_import"
        : phaseId === "runner"
          ? "runner_health"
          : null;
  if (!stageName) return null;
  const report = install.smoke_test_report;
  if (!report || typeof report !== "object") return null;
  const stage = (report as Record<string, unknown>)[stageName];
  if (!stage || typeof stage !== "object") return null;
  const status = (stage as Record<string, unknown>).status;
  if (status === "passed" || status === "failed" || status === "blocked") return status;
  return null;
}

function preparationPhaseStatusLabel(status: PreparationPhaseStatus) {
  switch (status) {
    case "passed":
      return "Done";
    case "active":
      return "Working";
    case "failed":
      return "Failed";
    case "blocked":
      return "Blocked";
    default:
      return "Waiting";
  }
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

function workflowHandleSource(job: EngineJob): WorkflowRuntimeHandleSource {
  if (job.status === "queued_pending_memory" && job.engine === "noofy") {
    return "workflow_run_queue";
  }
  return "job";
}

function runnerIdFromLease(runner: Record<string, unknown> | null) {
  const runnerId = runner?.runner_id;
  return typeof runnerId === "string" ? runnerId : null;
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

interface RunDisabledReasonInput {
  backendKnownUnreachable: boolean;
  engineKnownUnavailable: boolean;
  installStatus: string | null;
  isBlockedByMemory: boolean;
  isRunning: boolean;
  isWaitingForMemory: boolean;
  loading: boolean;
  missingModels: Array<{ filename: string }>;
  modelSummaryReady: boolean | undefined;
  validation: WorkflowValidationResult | null;
  workflowStatus: WorkflowStatusResponse | null;
}

function workflowRunDisabledReason({
  backendKnownUnreachable,
  engineKnownUnavailable,
  installStatus,
  isBlockedByMemory,
  isRunning,
  isWaitingForMemory,
  loading,
  missingModels,
  modelSummaryReady,
  validation,
  workflowStatus,
}: RunDisabledReasonInput): string {
  if (isRunning) return "This workflow is already running.";
  if (isBlockedByMemory) return "Not enough memory is available for this run.";
  if (isWaitingForMemory) return "Noofy is waiting for memory to free up.";
  if (backendKnownUnreachable) return "The local app service is offline.";
  if (engineKnownUnavailable) return "The ComfyUI engine is not ready yet.";
  if (installStatus === "unsupported" || workflowStatus?.can_prepare === false) {
    return "This workflow cannot run on this machine.";
  }
  if (missingModels.length > 0) {
    const names = missingModels.slice(0, 2).map((model) => model.filename).join(", ");
    const remaining = missingModels.length > 2 ? ` and ${missingModels.length - 2} more` : "";
    return `Add required model before running: ${names}${remaining}.`;
  }
  if (modelSummaryReady === false) return "This workflow needs required models before it can run.";
  if (validation && !validation.valid) {
    return validation.errors[0] ?? "This workflow needs setup before it can run.";
  }
  if (loading || !workflowStatus || !validation) return "Checking workflow readiness...";
  return "This workflow is not ready to run.";
}

function activeRequiredModelSummary(
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

function defaultValueForWorkflowInput(input: WorkflowInputDef): unknown {
  if (input.control === "lora_loader" && isEmptyWorkflowValue(input.default)) return "None";
  return input.default;
}

function normalizedLoraInputValues(
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

function activeWorkflowValidation(
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
  "not_enough_disk_space",
]);

function requiredModelDownloadSelections(
  summary: RequiredModelSummary | null,
  workflowId: string,
): ModelDownloadSelection[] {
  if (!summary) return [];
  return summary.models
    .filter((model) => retryableRequiredModelStatuses.has(model.status))
    .map((model) => ({ workflow_id: workflowId, requirement_id: model.requirement_id }));
}

function WorkflowRequiredModelsModal({
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
  const activeDownload = Boolean(downloadJob && ["queued", "running"].includes(downloadJob.status));
  const activeVerification = Boolean(verificationJob && ["queued", "running"].includes(verificationJob.status));
  const downloadable = effectiveSummary.models.some((model) => retryableRequiredModelStatuses.has(model.status));
  const progressByRequirement = new Map(downloadJob?.models.map((model) => [model.requirement_id, model]) ?? []);
  const readyToRun = effectiveSummary.ready_to_run;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="workflow-required-models-title">
      <section className="required-models-modal">
        <header className="required-models-modal__header">
          <div>
            <p className="eyebrow">Workflow models</p>
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
        {downloadJob && shouldShowModelDownloadProgress(downloadJob) ? <WorkflowModelDownloadProgress job={downloadJob} /> : null}
        {downloadError ? (
          <div className="notice notice--error notice--compact" role="status">
            <AlertCircle size={16} aria-hidden="true" />
            <div>
              <strong>Model download failed</strong>
              <span>{downloadError}</span>
            </div>
          </div>
        ) : null}

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
        <p>{[friendlyRequiredModelType(model.model_type), formatRequiredModelSize(model.size_bytes)].filter(Boolean).join(" · ")}</p>
        {message ? <span className="required-model-row__message">{message}</span> : null}
      </div>
      <div className="required-model-row__meta">
        <span className="model-identity">{requiredModelVerificationLabel(model.verification_level)}</span>
        <span className={`model-status-pill model-status-pill--${status}`}>{statusLabel}</span>
        <span className="model-source">{requiredModelSourceLabel(model)}</span>
      </div>
    </article>
  );
}

function WorkflowModelDownloadProgress({ job }: { job: ModelDownloadJobStatus }) {
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
  const percentLabel = percent !== null
    ? `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`
    : job.status;

  return (
    <div className="model-download-progress" role="status">
      <div className="model-download-progress__header">
        <strong>{label}</strong>
        <span>{percentLabel}</span>
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
    </div>
  );
}

function shouldShowModelDownloadProgress(job: ModelDownloadJobStatus) {
  if (["queued", "running", "completed", "failed", "canceled"].includes(job.status)) return true;
  return job.percent !== null || job.bytes_downloaded !== null;
}

function hasVerifiableLocalModels(summary: RequiredModelSummary | null) {
  return Boolean(summary?.models.some((model) => model.status === "possible_match"));
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

function friendlyRequiredModelType(type?: string | null) {
  const normalized = (type ?? "").toLowerCase();
  if (!normalized) return "AI model";
  if (normalized.includes("checkpoint")) return "AI model";
  if (normalized.includes("lora")) return "Style add-on";
  if (normalized.includes("controlnet")) return "Guidance model";
  if (normalized.includes("vae")) return "Image helper";
  if (normalized.includes("upscale")) return "Upscale model";
  return "AI model";
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

function dashboardUserStateVersion(packageData: WorkflowPackageResponse | null): string {
  if (!packageData) return "";

  const valueStateShape = {
    inputs: packageData.inputs.map((input) => ({
      id: input.id,
      control: input.control,
      binding: input.binding,
      default: input.default,
      validation: input.validation,
    })),
    controls: packageData.dashboard.sections.flatMap((section) =>
      section.controls.map((control) => ({
        id: control.id,
        type: control.type,
        input_id: control.input_id,
        output_id: control.output_id,
      })),
    ),
    groups: packageData.dashboard.sections.flatMap((section) =>
      (section.groups ?? []).map((group) => ({
        id: group.id,
        control_ids: group.control_ids,
        layout: group.layout,
      })),
    ),
  };

  return `${packageData.dashboard.version}:${hashString(stableJson(valueStateShape))}`;
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function hashString(value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return (hash >>> 0).toString(36);
}

function actionBarPositionFromDashboard(
  position: { x?: unknown; y?: unknown } | null | undefined,
): CanvasActionBarPosition | null {
  if (!position || typeof position !== "object") return null;
  const candidate = position as { x?: unknown; y?: unknown };
  if (typeof candidate.x !== "number" || typeof candidate.y !== "number") return null;
  if (!Number.isFinite(candidate.x) || !Number.isFinite(candidate.y)) return null;
  return {
    x: Math.max(0, Math.round(candidate.x)),
    y: Math.max(0, Math.round(candidate.y)),
  };
}

function dashboardSavePayloadWithActionBarPosition(
  packageData: WorkflowPackageResponse,
  position: CanvasActionBarPosition,
): DashboardSavePayload {
  return {
    inputs: packageData.inputs,
    dashboard: {
      ...packageData.dashboard,
      status: "configured",
      outputs: packageData.outputs,
      presentation: {
        ...(packageData.dashboard.presentation ?? {}),
        action_bar: {
          x: Math.max(0, Math.round(position.x)),
          y: Math.max(0, Math.round(position.y)),
        },
      },
    },
  };
}

function updatePackageActionBarPosition(
  current: RunPageState,
  position: CanvasActionBarPosition,
): RunPageState {
  if (!current.packageData) return current;
  return {
    ...current,
    packageData: {
      ...current.packageData,
      dashboard: {
        ...current.packageData.dashboard,
        presentation: {
          ...(current.packageData.dashboard.presentation ?? {}),
          action_bar: {
            x: Math.max(0, Math.round(position.x)),
            y: Math.max(0, Math.round(position.y)),
          },
        },
      },
    },
  };
}

function buildDashboardSchemaForEditing(
  workflowId: string,
  workflowName: string,
  controls: DashboardControlDef[],
  groups: DashboardControlGroupDef[],
  inputIndex: Map<string, WorkflowInputDef>,
  outputIndex: Map<string, WorkflowOutputDef>,
  layoutOverrides: Record<string, GridItemLayout>,
  actionBarPosition: CanvasActionBarPosition | null,
): DashboardSchema | null {
  const widgets: DashboardWidget[] = [];
  const groupedControlIds = groupedControlIdSet(groups);
  const controlTypeById = new Map(controls.map((control) => [control.id, control.type]));

  for (const control of controls) {
    const layout = groupedControlIds.has(control.id)
      ? undefined
      : layoutForBuilderControl(control, layoutOverrides[control.id]);

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
        defaultValue: null,
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
    groups: groups.map((group) => {
      const override = layoutOverrides[group.id];
      const childTypes = group.control_ids
        .map((controlId) => controlTypeById.get(controlId))
        .filter((type): type is string => Boolean(type));
      const layout = layoutForBuilderGroup(group, childTypes, override);
      return {
        id: group.id,
        title: group.title,
        description: group.description ?? "",
        widgetIds: group.control_ids,
        layout,
      };
    }),
    layout: {
      gridColumns: 32,
      rowHeight: 32,
      gridGap: 14,
      responsive: true,
    },
    presentation: actionBarPosition ? { actionBar: actionBarPosition } : undefined,
  };
}

function layoutForBuilderGroup(
  group: DashboardControlGroupDef,
  childTypes: string[],
  override?: GridItemLayout,
): DashboardWidget["layout"] {
  const fallback = defaultLayoutForWidgetGroup(childTypes);
  if (override) {
    const minW = override.minW ?? group.layout?.min_w ?? fallback.minW;
    const minH = override.minH ?? group.layout?.min_h ?? fallback.minH;
    return {
      x: override.x,
      y: override.y,
      w: Math.max(override.w, minW ?? 2),
      h: Math.max(override.h, minH ?? 2),
      minW,
      minH,
    };
  }

  if (!group.layout) return undefined;
  const minW = group.layout.min_w ?? fallback.minW;
  const minH = group.layout.min_h ?? fallback.minH;
  return {
    x: group.layout.x,
    y: group.layout.y,
    w: Math.max(group.layout.w, minW ?? 2),
    h: Math.max(group.layout.h, minH ?? 2),
    minW,
    minH,
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

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const options = value.filter((option): option is string => typeof option === "string" && option.length > 0);
  return options.length > 0 ? options : undefined;
}
