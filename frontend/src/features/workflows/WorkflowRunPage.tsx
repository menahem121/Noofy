import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Clipboard,
  CheckCircle2,
  Download,
  File as FileIcon,
  FileJson,
  Image,
  Loader2,
  Play,
  RotateCcw,
  Share2,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";

import {
  cancelModelDownload,
  cancelWorkflowActiveAndQueuedRuns,
  copyGalleryImageToDashboardAsset,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchAssetBlobUrl,
  fetchModelDownloadStatus,
  fetchApiKeySettings,
  fetchJobLogs,
  fetchJobGalleryStatus,
  fetchJobProgress,
  fetchJobResult,
  fetchLogs,
  fetchWorkflowActiveAndQueuedRuns,
  fetchWorkflowModelVerificationStatus,
  fetchWorkflowModelSummary,
  fetchWorkflowPackage,
  fetchWorkflowStatus,
  isEngineJob,
  saveJobOutputToGallery,
  cancelJobOutputGallerySave,
  closeWorkflowRunnerLease,
  openWorkflowRunnerLease,
  resetDashboardCustomization,
  resolveBackendUrl,
  runWorkflow,
  saveDashboard,
  startWorkflowModelVerification,
  startModelDownload,
  uploadDashboardAsset,
  uploadDashboardAudioAsset,
  uploadDashboardFileAsset,
  uploadDashboardImageMaskAsset,
  uploadDashboardVideoAsset,
  uploadDashboardThreeDAsset,
  validateWorkflow,
  type DashboardSavePayload,
  type DashboardControlDef,
  type DashboardControlGroupDef,
  type DiagnosticEvent,
  type EngineJob,
  type ApiKeySettingsResponse,
  type JobProgress,
  type JobResult,
  type GallerySaveRequest,
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
  type UploadProgress,
} from "../../lib/api/noofyApi";
import {
  canPreserveWidgetAsHiddenInput,
  type DashboardSchema,
  type DashboardWidget,
  type WidgetType,
} from "../dashboard-builder/dashboardBuilderContent";
import type { GridItemLayout } from "../../lib/gridLayout";
import { defaultLayoutForWidgetGroup, defaultLayoutForWidgetType } from "../../lib/widgetSizes";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { useWorkflowUserState } from "../../lib/useWorkflowUserState";
import { workflowDisplayName } from "../../lib/workflowNames";
import { audioMetadataLabel, fileMetadataLabel, videoMetadataLabel, type OutputAudioMedia, type OutputFileMedia, type OutputThreeDMedia, type OutputVideoMedia } from "./media";
import { ThreeDViewer } from "../three-d/ThreeDViewer";
import {
  failedModelMessage,
  isModelDownloadActive,
  isModelDownloadFailure,
  modelDownloadPanelTone,
  modelDownloadPercentLabel,
} from "../../lib/modelDownloadProgress";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { useOptionalWorkflowTabs, type WorkflowRuntimeHandleSource, type WorkflowTabRuntimeState } from "../app/WorkflowTabs";
import { CanvasDashboardView, type CanvasActionBarPosition } from "./CanvasDashboardView";
import { GallerySaveAction } from "./GallerySaveAction";
import { CivitaiLoraBrowserModal } from "./CivitaiLoraBrowserModal";
import { ImageComparisonSlider } from "./ImageComparisonSlider";
import { ModelReferenceDetails } from "./ModelReferenceDetails";
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
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
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

interface FailedTrackedRun {
  handle: string;
  jobId: string | null;
  message: string;
}

interface WorkflowCancelConfirmationState {
  count: number;
}

interface TrackedRunBase {
  clientId: string;
  status: JobProgress["status"] | string;
  submittedAt: number;
  updatedAt: number;
  lastPolledAt: number | null;
  message: string | null;
}

type TrackedRun =
  | (TrackedRunBase & { type: "queue"; queueId: string; jobId?: string | null })
  | (TrackedRunBase & { type: "job"; jobId: string; queueId?: string | null });

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
const activeWorkflowProgressStatuses = new Set(["queued", "running", "queued_pending_memory"]);
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
const comparisonImageInputControlTypes = new Set(["load_image", "load_image_mask"]);
const optimisticJobId = "__pending_workflow_run__";
const logLimit = 200;

export function WorkflowRunPage({
  workflowId,
  onBack,
  onWorkflowNameChange,
  onEditWidgets,
  onConfigureDashboard,
  onNavigate,
}: WorkflowRunPageProps) {
  const [state, setState] = useState<RunPageState>(initialState);
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [failureDialog, setFailureDialog] = useState<RunFailureDialogState | null>(null);
  const [failedTrackedRuns, setFailedTrackedRuns] = useState<FailedTrackedRun[]>([]);
  const [failedRunSummaryOpen, setFailedRunSummaryOpen] = useState(false);
  const [workflowCancelConfirmation, setWorkflowCancelConfirmation] = useState<WorkflowCancelConfirmationState | null>(null);
  const [batchCount, setBatchCount] = useState(1);
  const [trackedRuns, setTrackedRuns] = useState<TrackedRun[]>([]);
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
  const [runComparisonInputAssetId, setRunComparisonInputAssetId] = useState<string | null>(null);
  const [gallerySaveByControlId, setGallerySaveByControlId] = useState<Record<string, GallerySaveRequest>>({});
  const [comparisonInputImageUrl, setComparisonInputImageUrl] = useState<string | null>(null);
  const trackedRunsRef = useRef<TrackedRun[]>([]);
  const trackedRunPollInFlightRef = useRef<Set<string>>(new Set());
  const runSubmissionInFlightCountRef = useRef(0);
  const modelVerificationStartInFlightRef = useRef(false);
  const dashboardSetupRouteRequestedRef = useRef<string | null>(null);
  const runnerLeaseOpenedForRef = useRef<string | null>(null);

  useEffect(() => {
    trackedRunsRef.current = trackedRuns;
  }, [trackedRuns]);

  const { viewMode } = useAppPreferences();
  const runtimeStatus = useRuntimeStatus();
  const workflowTabs = useOptionalWorkflowTabs();
  const workflowRuntime = workflowTabs?.runtimeByWorkflowId[workflowId] ?? null;
  const runtimeProgress = progressFromWorkflowRuntime(workflowRuntime);
  const remainingTrackedRunCount = trackedRuns.filter(isTrackedRunActive).length;
  const currentTrackedRun = selectCurrentTrackedRun(trackedRuns);
  const displayedProgress = progressFromTrackedRun(currentTrackedRun, state.progress ?? runtimeProgress) ?? state.progress ?? runtimeProgress;
  const hasTerminalProgress = Boolean(displayedProgress?.status && terminalStatuses.has(displayedProgress.status));
  const activeJobStatus = hasTerminalProgress ? null : state.job?.status;
  const activeRuntimeJobId = currentTrackedRun ? trackedRunHandle(currentTrackedRun) : workflowRuntime?.activeJobId ?? workflowRuntime?.queueId ?? null;
  const isRunning = isSubmittingRun || remainingTrackedRunCount > 0 || isActiveWorkflowProgress(displayedProgress);
  const isWaitingForMemory = activeJobStatus === "queued_pending_memory" || displayedProgress?.status === "queued_pending_memory";
  const isBlockedByMemory = activeJobStatus === "blocked_by_memory";
  const outputImages = useMemo(() => extractImageUrls(state.result), [state.result]);
  const outputAudios = useMemo(() => extractAudioOutputs(state.result), [state.result]);
  const outputVideos = useMemo(() => extractVideoOutputs(state.result), [state.result]);
  const outputFiles = useMemo(() => extractFileOutputs(state.result), [state.result]);
  const outputThreeDs = useMemo(() => extractThreeDOutputs(state.result), [state.result]);

  useEffect(() => {
    setComparisonInputImageUrl(null);
    if (!runComparisonInputAssetId) return undefined;

    let canceled = false;
    let objectUrl: string | null = null;
    fetchAssetBlobUrl(runComparisonInputAssetId)
      .then((url) => {
        if (canceled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setComparisonInputImageUrl(url);
      })
      .catch(() => {
        if (!canceled) setComparisonInputImageUrl(null);
      });

    return () => {
      canceled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [runComparisonInputAssetId]);

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
        if (mediaOutputKind(image, "image") === "image" && image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
          imageUrls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
        }
      }
      if (imageUrls.length > 0) {
        map.set(nodeId, [...(map.get(nodeId) ?? []), ...imageUrls]);
      }
    }
    return map;
  }, [state.result]);

  const outputAudiosByNodeId = useMemo<Map<string, OutputAudioMedia[]>>(() => {
    const map = new Map<string, OutputAudioMedia[]>();
    if (!state.result) return map;
    for (const output of state.result.outputs) {
      const outputPayload = output.output;
      if (!outputPayload || typeof outputPayload !== "object") continue;
      const nodeIdKey = Object.keys(output).find((k) => k !== "output");
      const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey;
      if (!nodeId) continue;
      const audios = (outputPayload as Record<string, unknown>).audio;
      if (!Array.isArray(audios)) continue;
      const audioOutputs = audios.map(normalizeAudioOutput).filter((item): item is OutputAudioMedia => Boolean(item));
      if (audioOutputs.length > 0) {
        map.set(nodeId, [...(map.get(nodeId) ?? []), ...audioOutputs]);
      }
    }
    return map;
  }, [state.result]);

  const outputVideosByNodeId = useMemo<Map<string, OutputVideoMedia[]>>(
    () => extractVideoOutputsByNodeId(state.result),
    [state.result],
  );
  const outputFilesByNodeId = useMemo<Map<string, OutputFileMedia[]>>(
    () => extractFileOutputsByNodeId(state.result),
    [state.result],
  );
  const outputThreeDsByNodeId = useMemo<Map<string, OutputThreeDMedia[]>>(
    () => extractThreeDOutputsByNodeId(state.result),
    [state.result],
  );
  const classicPreviewMedia = useMemo(
    () => selectClassicPreviewMedia(allControls, state.packageData?.outputs ?? [], outputImagesByNodeId, outputAudiosByNodeId, outputVideosByNodeId, outputThreeDsByNodeId, outputFilesByNodeId, outputImages, outputAudios, outputVideos, outputThreeDs, outputFiles),
    [allControls, outputAudios, outputAudiosByNodeId, outputFiles, outputFilesByNodeId, outputImages, outputImagesByNodeId, outputThreeDs, outputThreeDsByNodeId, outputVideos, outputVideosByNodeId, state.packageData?.outputs],
  );
  const hasActiveGallerySave = Object.values(gallerySaveByControlId).some(
    (item) => item.status === "queued" || item.status === "saving",
  );

  useEffect(() => {
    const jobId = state.result?.status === "completed" ? state.result.job_id : null;
    if (!jobId) {
      setGallerySaveByControlId({});
      return;
    }
    let stopped = false;
    const refresh = async () => {
      try {
        const response = await fetchJobGalleryStatus(jobId);
        if (stopped) return;
        setGallerySaveByControlId(Object.fromEntries(response.outputs.map((item) => [item.control_id, item])));
      } catch {
        // Saving remains optional to the completed workflow result.
      }
    };
    void refresh();
    return () => {
      stopped = true;
    };
  }, [state.result?.job_id, state.result?.status]);

  useEffect(() => {
    const jobId = state.result?.status === "completed" ? state.result.job_id : null;
    if (!jobId || !hasActiveGallerySave) return undefined;
    const interval = window.setInterval(() => {
      fetchJobGalleryStatus(jobId)
        .then((response) => setGallerySaveByControlId(Object.fromEntries(response.outputs.map((item) => [item.control_id, item]))))
        .catch(() => undefined);
    }, 700);
    return () => window.clearInterval(interval);
  }, [hasActiveGallerySave, state.result?.job_id, state.result?.status]);

  async function handleSaveOutputToGallery(controlId: string) {
    if (state.result?.status !== "completed") return;
    try {
      const request = await saveJobOutputToGallery(state.result.job_id, controlId);
      setGallerySaveByControlId((current) => ({ ...current, [controlId]: request }));
    } catch (error) {
      setGallerySaveByControlId((current) => ({
        ...current,
        [controlId]: failedGallerySaveRequest(state.result!.job_id, controlId, error),
      }));
    }
  }

  async function handleCancelOutputGallerySave(controlId: string) {
    if (state.result?.status !== "completed") return;
    try {
      const request = await cancelJobOutputGallerySave(state.result.job_id, controlId);
      setGallerySaveByControlId((current) => ({ ...current, [controlId]: request }));
    } catch {
      // Keep polling: the background save may still complete or accept a later cancel.
    }
  }

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
    setRunComparisonInputAssetId(null);
    trackedRunsRef.current = [];
    setTrackedRuns([]);
    setFailedTrackedRuns([]);
    setFailedRunSummaryOpen(false);
    setWorkflowCancelConfirmation(null);
  }, [workflowId, runtimeStatus.refreshRuntime]);

  useEffect(() => {
    if (!modelDownloadJob || !isModelDownloadActive(modelDownloadJob.status)) return;
    const interval = window.setInterval(() => {
      fetchModelDownloadStatus(modelDownloadJob.job_id)
        .then((job) => {
          setModelDownloadJob(job);
          setModelDownloadError(null);
          if (!isModelDownloadActive(job.status)) {
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

  useEffect(() => {
    if (remainingTrackedRunCount === 0 || isSubmittingRun || runSubmissionInFlightCountRef.current > 0) return undefined;
    let stopped = false;
    const poll = () => {
      if (!stopped) void pollTrackedRunsDue();
    };
    poll();
    const interval = window.setInterval(poll, 1000);
    return () => {
      stopped = true;
      window.clearInterval(interval);
    };
  }, [isSubmittingRun, remainingTrackedRunCount]);

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

  function beginRunSubmission() {
    runSubmissionInFlightCountRef.current += 1;
    setIsSubmittingRun(true);
  }

  function finishRunSubmission() {
    runSubmissionInFlightCountRef.current = Math.max(0, runSubmissionInFlightCountRef.current - 1);
    setIsSubmittingRun(runSubmissionInFlightCountRef.current > 0);
  }

  function cancelRunSubmissions() {
    runSubmissionInFlightCountRef.current = 0;
    setIsSubmittingRun(false);
  }

  async function handleRun() {
    if (!canRun) {
      return;
    }

    const shouldTrackPreparation = shouldShowRunPreparationDialog(state.workflowStatus);
    let stopPreparationPolling: (() => void) | null = null;
    const runCount = clampBatchCount(batchCount);
    const submittedValuesSnapshot = { ...submittedInputValues };
    const outputPreferencesSnapshot = getOutputPreferencesSnapshot();
    const submittedComparisonInputAssetId = comparisonImageAssetIdForRun(
      state.packageData,
      allControls,
      submittedValuesSnapshot,
    );
    beginRunSubmission();
    setRunComparisonInputAssetId(submittedComparisonInputAssetId);
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
      error: null,
    }));

    try {
      for (let index = 0; index < runCount; index += 1) {
        const response = await runWorkflow(workflowId, {
          inputs: submittedValuesSnapshot,
          options: {},
          output_preferences_snapshot: outputPreferencesSnapshot,
        });

        if (!isEngineJob(response)) {
          stopPreparationPolling?.();
          finishRunSubmission();
          setRunPreparationDialog(null);
          setRunComparisonInputAssetId(null);
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

        setSubmittedJob(response);
        if (isTrackableJob(response)) {
          addTrackedRun(trackedRunFromJob(response));
        } else {
          stopPreparationPolling?.();
          finishRunSubmission();
          setRunPreparationDialog(null);
          return;
        }
      }
      stopPreparationPolling?.();
      finishRunSubmission();
      setRunPreparationDialog(null);
      void pollTrackedRunsDue(true);
    } catch (error) {
      stopPreparationPolling?.();
      const message = error instanceof Error ? error.message : String(error);
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      finishRunSubmission();
      setRunPreparationDialog(null);
      setRunComparisonInputAssetId(null);
      setState((current) => ({
        ...current,
        job: null,
        progress: null,
        error: message,
      }));
      void openFailureDialog(message, null);
    }
  }

  async function handleCancel() {
    const trackedCount = cancelableWorkflowRunCount(trackedRunsRef.current);
    const fallbackCount = Math.max(trackedCount, activeRuntimeJobId ? 1 : 0);
    let count = fallbackCount;
    try {
      const summary = await fetchWorkflowActiveAndQueuedRuns(workflowId);
      count = Math.max(summary.total_count, fallbackCount);
    } catch (error) {
      if (fallbackCount <= 0) {
        setState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : String(error),
        }));
        return;
      }
    }
    if (count <= 0) return;
    if (count > 1) {
      setWorkflowCancelConfirmation({ count });
      return;
    }
    await cancelWorkflowRunsForCurrentWorkflow();
  }

  async function cancelWorkflowRunsForCurrentWorkflow() {
    try {
      await cancelWorkflowActiveAndQueuedRuns(workflowId);
      cancelRunSubmissions();
      const progress = workflowCancelProgress(workflowId);
      replaceTrackedRuns(
        trackedRunsRef.current.map((run) => isTrackedRunActive(run) ? trackedRunWithStatus(run, "canceled", "Workflow run canceled.") : run),
      );
      setWorkflowCancelConfirmation(null);
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

  async function handleGalleryImageMaskPrepare(inputId: string, galleryItemId: string) {
    const { asset_id } = await copyGalleryImageToDashboardAsset(workflowId, inputId, galleryItemId);
    setInputValue(inputId, asset_id);
    return asset_id;
  }

  async function handleImageMaskApply(sourceAssetId: string, mask: Blob) {
    const { asset_id } = await uploadDashboardImageMaskAsset(workflowId, sourceAssetId, mask);
    return asset_id;
  }

  async function handleAudioUpload(inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) {
    const { asset_id } = await uploadDashboardAudioAsset(workflowId, file, onProgress, signal);
    setInputValue(inputId, asset_id);
  }

  async function handleVideoUpload(inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) {
    const { asset_id } = await uploadDashboardVideoAsset(workflowId, file, onProgress, signal);
    setInputValue(inputId, asset_id);
  }

  async function handleFileUpload(inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) {
    const { asset_id } = await uploadDashboardFileAsset(workflowId, inputId, file, onProgress, signal);
    setInputValue(inputId, asset_id);
  }

  async function handleThreeDUpload(inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) {
    const { asset_id } = await uploadDashboardThreeDAsset(workflowId, file, onProgress, signal);
    setInputValue(inputId, asset_id);
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

  function replaceTrackedRuns(nextRuns: TrackedRun[]) {
    trackedRunsRef.current = nextRuns;
    setTrackedRuns(nextRuns);
  }

  function addTrackedRun(run: TrackedRun) {
    const nextRuns = [...trackedRunsRef.current, run];
    replaceTrackedRuns(nextRuns);
    recordWorkflowTrackedRuns(nextRuns);
  }

  async function pollTrackedRunsDue(force = false) {
    const now = Date.now();
    const runs = trackedRunsRef.current.filter(isTrackedRunActive);
    if (runs.length === 0) return;
    const current = selectCurrentTrackedRun(runs);
    const due: TrackedRun[] = [];
    if (current && (force || current.lastPolledAt === null || now - current.lastPolledAt >= 1000)) {
      due.push(current);
    }
    const queuedDue = runs
      .filter((run) => run.clientId !== current?.clientId)
      .filter((run) => force || run.lastPolledAt === null || now - run.lastPolledAt >= 4500)
      .slice(0, 3);
    due.push(...queuedDue);

    for (const run of due) {
      const handle = trackedRunHandle(run);
      if (trackedRunPollInFlightRef.current.has(handle)) continue;
      trackedRunPollInFlightRef.current.add(handle);
      markTrackedRunPolled(run.clientId, now);
      void pollTrackedRun(run).finally(() => {
        trackedRunPollInFlightRef.current.delete(handle);
      });
    }
  }

  async function pollTrackedRun(run: TrackedRun) {
    const handle = trackedRunHandle(run);
    try {
      const progress = await fetchJobProgress(handle);
      const nextRun = trackedRunFromProgress(run, progress, Date.now());
      upsertTrackedRun(nextRun, progress);
      setState((current) => ({ ...current, progress, error: null }));

      if (!terminalStatuses.has(progress.status)) return;
      if (isQueueOnlyTerminal(nextRun, progress)) {
        handleTrackedTerminalProgress(nextRun, progress);
        return;
      }

      const result = await fetchJobResult(trackedRunHandle(nextRun));
      if (isEngineJob(result)) {
        setSubmittedJob(result);
        if (isTrackableJob(result)) {
          upsertTrackedRun(trackedRunFromJob(result, nextRun.clientId));
        }
        return;
      }
      handleTrackedResult(nextRun, result);
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "Could not check workflow progress.",
      }));
    }
  }

  function markTrackedRunPolled(clientId: string, polledAt: number) {
    replaceTrackedRuns(
      trackedRunsRef.current.map((run) => run.clientId === clientId ? { ...run, lastPolledAt: polledAt } : run),
    );
  }

  function upsertTrackedRun(nextRun: TrackedRun, knownProgress: JobProgress | null = null) {
    const nextRuns = trackedRunsRef.current.map((run) => run.clientId === nextRun.clientId ? nextRun : run);
    replaceTrackedRuns(nextRuns);
    recordWorkflowTrackedRuns(nextRuns, knownProgress);
  }

  function handleTrackedTerminalProgress(run: TrackedRun, progress: JobProgress) {
    const nextRun = trackedRunWithStatus(run, progress.status, progress.message);
    upsertTrackedRun(nextRun);
    if (progress.status === "failed") {
      recordTrackedFailure(trackedRunHandle(nextRun), null, progress.message ?? "Workflow run failed.");
    }
    pollNextTrackedRunAfterTerminal();
  }

  function handleTrackedResult(run: TrackedRun, result: JobResult) {
    const nextRun = trackedRunWithStatus(run, result.status, result.error);
    upsertTrackedRun(nextRun);
    if (result.status === "completed") {
      setState((current) => ({ ...current, result }));
    } else if (result.status === "failed") {
      setState((current) => ({ ...current, result }));
      recordTrackedFailure(trackedRunHandle(nextRun), result.job_id, result.error ?? "The local engine could not finish this run.");
    }
    if (!selectCurrentTrackedRun(trackedRunsRef.current)) {
      recordWorkflowTerminalResult(result);
    }
    pollNextTrackedRunAfterTerminal();
  }

  function pollNextTrackedRunAfterTerminal() {
    if (selectCurrentTrackedRun(trackedRunsRef.current)) {
      void pollTrackedRunsDue(true);
    }
  }

  function recordTrackedFailure(handle: string, jobId: string | null, message: string) {
    setFailedTrackedRuns((current) => {
      if (current.some((item) => item.handle === handle)) return current;
      const next = [...current, { handle, jobId, message }];
      if (next.length === 1) {
        void openFailureDialog(message, jobId ?? handle);
      } else if (next.length > 1) {
        setFailureDialog(null);
      }
      return next;
    });
  }

  function setSubmittedJob(job: EngineJob) {
    const progress = progressFromSubmittedJob(job);
    recordWorkflowJob(job, progress);
    setState((current) => ({
      ...current,
      job,
      progress,
    }));
  }

  function recordWorkflowJob(job: EngineJob, progress: JobProgress) {
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: job.job_id,
      activeJobStatus: job.status,
      activeJobProgress: progress,
      activeJobUpdatedAt: Date.now(),
      handleSource: workflowHandleSource(job),
      queueId: job.queue_id ?? (job.status === "queued_pending_memory" ? job.job_id : null),
    });
  }

  function recordWorkflowTrackedRuns(runs: TrackedRun[], knownProgress: JobProgress | null = null) {
    const current = selectCurrentTrackedRun(runs);
    if (!current) {
      if (knownProgress && terminalStatuses.has(knownProgress.status)) {
        recordWorkflowProgress(knownProgress);
      }
      return;
    }
    const progress = progressFromTrackedRun(current, knownProgress);
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: trackedRunHandle(current),
      activeJobStatus: current.status,
      activeJobProgress: progress,
      activeJobUpdatedAt: Date.now(),
      handleSource: trackedRunHandleSource(current),
      queueId: current.queueId ?? null,
    });
  }

  function recordWorkflowProgress(progress: JobProgress) {
    if (terminalStatuses.has(progress.status)) {
      workflowTabs?.setWorkflowRuntime(workflowId, {
        activeJobId: null,
        activeJobStatus: progress.status,
        activeJobProgress: progress,
        activeJobUpdatedAt: Date.now(),
        handleSource: null,
        queueId: null,
      });
      return;
    }
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: progress.job_id,
      activeJobStatus: progress.status,
      activeJobProgress: progress,
      activeJobUpdatedAt: Date.now(),
    });
  }

  function recordWorkflowTerminalResult(result: JobResult) {
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: null,
      activeJobStatus: result.status,
      activeJobProgress: null,
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });
  }

  const unresolvedModelSummary = activeModelSummary?.models.filter((model) => model.status !== "available") ?? [];
  const missingModels = unresolvedModelSummary.length > 0 ? unresolvedModelSummary : activeValidation?.missing_models ?? [];
  const workflowSummary = state.workflowStatus?.workflow;
  const workflowNameSource = workflowSummary ?? state.packageData?.metadata ?? state.packageData;
  const workflowDisplayTitle = workflowDisplayName(workflowNameSource);
  const dashboardSetupRequired = Boolean(
    state.packageData && packageNeedsDashboardSetup(state.packageData, workflowSummary),
  );
  const trust = workflowSummary?.trust;

  useEffect(() => {
    if (!workflowNameSource) return;
    const name = workflowDisplayName(workflowNameSource);
    if (name) onWorkflowNameChange?.(name);
  }, [
    state.packageData?.display_name,
    state.packageData?.metadata?.display_name,
    state.packageData?.metadata?.name,
    workflowSummary?.display_name,
    workflowSummary?.name,
    workflowNameSource,
  ]);

  useLayoutEffect(() => {
    if (!dashboardSetupRequired || !state.packageData || !onConfigureDashboard) return;
    const redirectKey = [
      workflowId,
      state.packageData.dashboard.status,
      dashboardUserStateVersion(state.packageData),
    ].join(":");
    if (dashboardSetupRouteRequestedRef.current === redirectKey) return;
    dashboardSetupRouteRequestedRef.current = redirectKey;
    onConfigureDashboard(workflowId, workflowDisplayTitle);
  }, [dashboardSetupRequired, onConfigureDashboard, state.packageData, workflowDisplayTitle, workflowId]);

  useEffect(() => {
    if (!workflowTabs || !state.packageData || dashboardSetupRequired) return;
    if (runnerLeaseOpenedForRef.current === workflowId) return;
    let canceled = false;
    runnerLeaseOpenedForRef.current = workflowId;
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
      runnerLeaseOpenedForRef.current = null;
    };
  }, [workflowId, Boolean(state.packageData), dashboardSetupRequired]);

  const installStatus = typeof state.workflowStatus?.install?.status === "string"
    ? state.workflowStatus.install.status
    : null;
  const memoryStatus = remainingTrackedRunCount === 0 && hasTerminalProgress ? null : state.job?.memory_status ?? null;
  const memoryNotice = memoryStatus ? memoryStatusDisplay(memoryStatus) : null;
  const memoryDiagnostics = memoryStatus ? memoryStatusDeveloperDetails(state.job) : null;
  const showMemoryLoadedPill = Boolean(memoryStatus && isWarmReusableMemoryState(memoryStatus.state));
  const showUserFacingMemoryNotice = Boolean(memoryNotice && !showMemoryLoadedPill && memoryNotice.title !== "Memory status");
  const backendKnownUnreachable = runtimeStatus.backendStatus === "unreachable";
  const engineKnownUnavailable =
    runtimeStatus.backendStatus === "reachable" &&
    (runtimeStatus.engineStatus === "offline" || runtimeStatus.engineStatus === "starting");
  const memoryRefusesRun = Boolean(memoryStatus && isBlockingMemoryState(memoryStatus.state));
  const canRun = Boolean(
    state.workflowStatus?.can_prepare !== false
      && activeValidation?.valid
      && activeModelSummary?.ready_to_run !== false
      && !backendKnownUnreachable
      && !engineKnownUnavailable
      && !isBlockedByMemory
      && !memoryRefusesRun,
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
        isRunning: false,
        isWaitingForMemory: false,
        loading: state.loading,
        memoryStatus,
        missingModels,
        modelSummaryReady: activeModelSummary?.ready_to_run,
        validation: activeValidation,
        workflowStatus: state.workflowStatus,
      });
  const canCancel = Boolean((remainingTrackedRunCount > 0 || (isRunning && (state.job || activeRuntimeJobId))) && !isBlockedByMemory);
  const progressPercent =
    displayedProgress?.value !== null && displayedProgress?.value !== undefined && displayedProgress.max
      ? Math.min(100, Math.round((displayedProgress.value / displayedProgress.max) * 100))
      : displayedProgress?.status === "completed"
        ? 100
        : 0;
  const cancelTooltip = remainingTrackedRunCount > 1
    ? "Cancel current run and all queued runs for this workflow"
    : "Cancel current run";
  const topBarProgress = isRunning ? {
    percent: progressPercent,
    remainingCount: remainingTrackedRunCount || undefined,
    onCancelRemaining: remainingTrackedRunCount > 0 ? () => void handleCancel() : undefined,
    cancelRemainingTitle: "Cancel current run and all queued runs for this workflow",
  } : null;

  const inputControls = allControls.filter(
    (c) => c.type === "note" || c.type === "api_credential" || (c.type !== "result_image" && c.type !== "display_image" && c.type !== "display_audio" && c.type !== "display_video" && c.type !== "display_file" && c.type !== "display_3d" && c.input_id),
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
      workflowDisplayName(workflowNameSource),
      allControls,
      allGroups,
      inputIndex,
      outputIndex,
      layoutOverrides,
      creatorActionBarPosition,
      inputValues,
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
          <p className="eyebrow">{workflowSummary?.source_label ?? "Workflow"}</p>
          {trust ? (
            <span className={`trust-badge trust-badge--${trust.badge_tone}`} title={trust.summary}>
              {trust.label}
            </span>
          ) : null}
        </div>
        <h1 id="workflow-title">{workflowDisplayName(workflowNameSource)}</h1>
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
            <strong>{runtimeStatus.engineStatus === "starting" ? "The local ComfyUI engine is starting" : "The local ComfyUI engine is not reachable"}</strong>
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
      {memoryStatus && showMemoryLoadedPill ? (
        <MemoryLoadedPill />
      ) : null}
      {memoryStatus && showUserFacingMemoryNotice ? (
        <div className={`notice ${memoryNoticeClass(memoryStatus)} notice--compact`} role="status">
          <AlertCircle size={16} aria-hidden="true" />
          <div>
            <strong>{memoryNotice?.title ?? memoryStatusTitle(memoryStatus.state)}</strong>
            <span>{memoryNotice?.message ?? memoryStatus.message}</span>
            {memoryDiagnostics ? (
              <details className="memory-status-developer-details">
                <summary>Developer details</summary>
                <pre>{memoryDiagnostics}</pre>
              </details>
            ) : null}
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
  const failedRunSummaryElement = failedTrackedRuns.length > 1 ? (
    <BatchFailureSummary
      failedRuns={failedTrackedRuns}
      expanded={failedRunSummaryOpen}
      onToggle={() => setFailedRunSummaryOpen((open) => !open)}
      onOpenLogs={(run) => void openFailureDialog(run.message, run.jobId ?? run.handle)}
    />
  ) : null;
  const workflowCancelConfirmationElement = workflowCancelConfirmation ? (
    <WorkflowCancelConfirmation
      count={workflowCancelConfirmation.count}
      onCancel={() => setWorkflowCancelConfirmation(null)}
      onConfirm={() => void cancelWorkflowRunsForCurrentWorkflow()}
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
  const exportReview: WorkflowExportReviewModel = {
    name: workflowDisplayTitle,
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
      workflowName={workflowDisplayTitle}
      exportUrl={exportDialog.url}
      extension={exportDialog.extension}
      inputValues={exportDialog.extension === ".json" ? submittedInputValues : undefined}
      review={exportDialog.extension === ".noofy" ? exportReview : undefined}
      onClose={() => setExportDialog(null)}
    />
  ) : null;
  const requiredModelsModalElement = requiredModelsModalOpen && activeModelSummary ? (
    <WorkflowRequiredModelsModal
      workflowName={workflowDisplayTitle}
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

  if (dashboardSetupRequired) {
    return (
      <AppLayout activeRoute="workflows" onNavigate={onNavigate}>
        <DashboardSetupRequired
          workflowName={workflowDisplayTitle}
          onBack={onBack}
          onContinue={
            onConfigureDashboard
              ? () => onConfigureDashboard(workflowId, workflowDisplayTitle)
              : undefined
          }
        />
      </AppLayout>
    );
  }

  if (showCanvasView) {
    return (
      <AppLayout
        activeRoute="workflows"
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
          outputAudiosByNodeId={outputAudiosByNodeId}
          outputVideosByNodeId={outputVideosByNodeId}
          outputFilesByNodeId={outputFilesByNodeId}
          outputThreeDsByNodeId={outputThreeDsByNodeId}
          comparisonBeforeImageUrl={comparisonInputImageUrl}
          inputValues={inputValues}
          outputPreferences={outputPreferences}
          gallerySaveByControlId={gallerySaveByControlId}
          layoutOverrides={draftLayoutOverrides ?? layoutOverrides}
          actionBarPosition={canvasActionBarPosition}
          isEditingLayout={isEditingLayout}
          runState={{
            isRunning,
            canRun,
            canCancel,
            memoryLoaded: showMemoryLoadedPill,
            cancelLabel: remainingTrackedRunCount > 1 ? "Cancel Runs" : "Cancel Run",
            cancelTitle: cancelTooltip,
            showStatusNotice: showUserFacingMemoryNotice,
            statusTitle: showUserFacingMemoryNotice ? memoryNotice?.title ?? null : null,
            statusMessage: showUserFacingMemoryNotice ? memoryNotice?.message ?? null : null,
            disabledReason: runDisabledReason,
            disabledActionLabel: hasRequiredModelFixAction ? "Download" : null,
            developerDetails: showUserFacingMemoryNotice || runDisabledReason ? memoryDiagnostics : null,
          }}
          batchCount={batchCount}
          exportNoofyUrl={exportWorkflowUrl(workflowId)}
          exportComfyJsonUrl={exportWorkflowComfyJsonUrl(workflowId)}
          exportWorkflowName={workflowDisplayTitle}
          exportReview={exportReview}
          onChange={(inputId, value) => setInputValue(inputId, value)}
          onImageUpload={handleImageUpload}
          onGalleryImageMaskPrepare={handleGalleryImageMaskPrepare}
          onImageMaskApply={handleImageMaskApply}
          onAudioUpload={handleAudioUpload}
          onVideoUpload={handleVideoUpload}
          onFileUpload={handleFileUpload}
          onThreeDUpload={handleThreeDUpload}
          loraBrowserFor={loraBrowserFor}
          onOutputPreferenceChange={(controlId, autoSave) => setOutputPreference(controlId, { auto_save: autoSave })}
          onSaveOutputToGallery={state.result?.status === "completed" ? (controlId) => void handleSaveOutputToGallery(controlId) : undefined}
          onCancelOutputGallerySave={state.result?.status === "completed" ? (controlId) => void handleCancelOutputGallerySave(controlId) : undefined}
          onRun={() => void handleRun()}
          onBatchCountChange={setBatchCount}
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
        {failedRunSummaryElement ? (
          <div className="canvas-run-floating-notices">
            {failedRunSummaryElement}
          </div>
        ) : null}
        {workflowCancelConfirmationElement}
        {failureDialogElement}
        {preparationDialogElement}
        {loraBrowserElement}
        {exportDialogElement}
        {requiredModelsModalElement}
      </AppLayout>
    );
  }

  return (
    <AppLayout activeRoute="workflows" onNavigate={onNavigate} progress={topBarProgress}>
      {pageHeader}
      {notices}
      {failedRunSummaryElement}

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
              onGalleryImageMaskPrepare={handleGalleryImageMaskPrepare}
              onImageMaskApply={handleImageMaskApply}
              onAudioUpload={handleAudioUpload}
              onVideoUpload={handleVideoUpload}
              onFileUpload={handleFileUpload}
              onThreeDUpload={handleThreeDUpload}
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
            <BatchCountStepper value={batchCount} onChange={setBatchCount} />
            <button className="primary-button" type="button" disabled={!canRun} onClick={() => void handleRun()}>
              {isRunning ? <Loader2 className="spin" size={18} aria-hidden="true" /> : <Play size={18} aria-hidden="true" />}
              Run Workflow
            </button>
            <button className="secondary-button" type="button" disabled={!canCancel} title={cancelTooltip} onClick={() => void handleCancel()}>
              <Square size={16} aria-hidden="true" />
              Cancel
            </button>
          </div>
        </form>

        <aside className="preview-panel">
          <div className="panel-heading">
            <div>
              <h2>Preview</h2>
              <p>{progressMessage(displayedProgress, state.result, memoryStatus)}</p>
            </div>
            {state.validation?.valid ? (
              <span className="mini-status">
                <CheckCircle2 size={13} aria-hidden="true" />
                Ready
              </span>
            ) : null}
          </div>

          <div className="preview-stage">
            {classicPreviewMedia?.kind === "image" ? (
              comparisonInputImageUrl ? (
                <ImageComparisonSlider
                  beforeSrc={comparisonInputImageUrl}
                  afterSrc={classicPreviewMedia.url}
                  alt="Generated workflow output"
                />
              ) : (
                <img src={classicPreviewMedia.url} alt="Generated workflow output" />
              )
            ) : classicPreviewMedia?.kind === "video" ? (
              <div className="preview-video-output">
                <video controls src={classicPreviewMedia.media.url} poster={classicPreviewMedia.media.thumbnailUrl ?? undefined} preload="metadata" />
                <strong>{classicPreviewMedia.media.filename}</strong>
                <span>{videoOutputMetaLabel(classicPreviewMedia.media)}</span>
              </div>
            ) : classicPreviewMedia?.kind === "audio" ? (
              <div className="preview-audio-output">
                <audio controls src={classicPreviewMedia.media.url} preload="metadata" />
                <strong>{classicPreviewMedia.media.filename}</strong>
                <span>{audioOutputMetaLabel(classicPreviewMedia.media)}</span>
              </div>
            ) : classicPreviewMedia?.kind === "3d" ? (
              <ThreeDViewer url={classicPreviewMedia.media.url} filename={classicPreviewMedia.media.filename} size={classicPreviewMedia.media.size} />
            ) : classicPreviewMedia?.kind === "file" ? (
              <div className="preview-file-output">
                <FileIcon size={28} aria-hidden="true" />
                <strong>{classicPreviewMedia.media.filename}</strong>
                <span>{fileOutputMetaLabel(classicPreviewMedia.media)}</span>
                <div className="preview-file-output__actions">
                  <button
                    className="secondary-button secondary-button--small"
                    type="button"
                    onClick={() => downloadMediaDirect(classicPreviewMedia.media.url, classicPreviewMedia.media.filename)}
                  >
                    <Download size={14} aria-hidden="true" />
                    Download
                  </button>
                  <button
                    className="secondary-button secondary-button--small"
                    type="button"
                    onClick={() => window.open(classicPreviewMedia.media.url, "_blank", "noopener,noreferrer")}
                  >
                    Open
                  </button>
                </div>
              </div>
            ) : (
              <div className="preview-empty">
                <Image size={48} aria-hidden="true" />
                <span>Your generated media will appear here.</span>
              </div>
            )}
          </div>

          {state.result?.status === "completed" && classicPreviewMedia?.controlId ? (
            <div className="preview-gallery-action">
              <GallerySaveAction
                status={gallerySaveByControlId[classicPreviewMedia.controlId]}
                onSave={() => void handleSaveOutputToGallery(classicPreviewMedia.controlId!)}
                onCancel={() => void handleCancelOutputGallerySave(classicPreviewMedia.controlId!)}
              />
            </div>
          ) : null}

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
      {workflowCancelConfirmationElement}
      {preparationDialogElement}
      {loraBrowserElement}
      {exportDialogElement}
      {requiredModelsModalElement}
    </AppLayout>
  );
}

function MemoryLoadedPill() {
  return (
    <div
      className="memory-loaded-pill"
      role="status"
      title="The required models are already loaded, so the next run should start faster."
    >
      <CheckCircle2 size={13} aria-hidden="true" />
      <span>Models loaded</span>
    </div>
  );
}

function BatchCountStepper({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const normalized = clampBatchCount(value);
  return (
    <div className="batch-count-stepper" aria-label="Batch count">
      <input
        type="number"
        min={1}
        max={99}
        value={normalized}
        aria-label="Batch count"
        onChange={(event) => onChange(clampBatchCount(Number(event.target.value)))}
      />
      <div className="batch-count-stepper__buttons">
        <button type="button" aria-label="Increase batch count" onClick={() => onChange(clampBatchCount(normalized + 1))}>
          <ChevronUp size={12} aria-hidden="true" />
        </button>
        <button type="button" aria-label="Decrease batch count" onClick={() => onChange(clampBatchCount(normalized - 1))}>
          <ChevronDown size={12} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function DashboardSetupRequired({
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

function packageNeedsDashboardSetup(
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

function BatchFailureSummary({
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

function WorkflowCancelConfirmation({
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
  onGalleryImageMaskPrepare,
  onImageMaskApply,
  onAudioUpload,
  onVideoUpload,
  onFileUpload,
  onThreeDUpload,
  loraBrowserFor,
}: {
  items: DashboardTopLevelControlItem[];
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onGalleryImageMaskPrepare: (inputId: string, galleryItemId: string) => Promise<string>;
  onImageMaskApply: (sourceAssetId: string, mask: Blob) => Promise<string>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onThreeDUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
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
                    onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
                    onImageMaskApply={onImageMaskApply}
                    onAudioUpload={onAudioUpload}
                    onVideoUpload={onVideoUpload}
                    onFileUpload={onFileUpload}
                    onThreeDUpload={onThreeDUpload}
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
            onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
            onImageMaskApply={onImageMaskApply}
            onAudioUpload={onAudioUpload}
            onVideoUpload={onVideoUpload}
            onFileUpload={onFileUpload}
            onThreeDUpload={onThreeDUpload}
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
  onGalleryImageMaskPrepare,
  onImageMaskApply,
  onAudioUpload,
  onVideoUpload,
  onFileUpload,
  onThreeDUpload,
  loraBrowserFor,
}: {
  control: DashboardControlDef;
  inputIndex: Map<string, WorkflowInputDef>;
  inputValues: Record<string, unknown>;
  grouped?: boolean;
  onChange: (id: string, value: unknown) => void;
  onImageUpload: (inputId: string, file: File) => Promise<void>;
  onGalleryImageMaskPrepare: (inputId: string, galleryItemId: string) => Promise<string>;
  onImageMaskApply: (sourceAssetId: string, mask: Blob) => Promise<string>;
  onAudioUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onVideoUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onFileUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  onThreeDUpload: (inputId: string, file: File, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal) => Promise<void>;
  loraBrowserFor?: (control: DashboardControlDef, input: WorkflowInputDef) => LoraBrowserControlProps | undefined;
}) {
        if (control.type === "note") {
          return (
            <section className="dashboard-note-card">
              <h3>{control.label}</h3>
              <p>{control.description || "No note text added yet."}</p>
            </section>
          );
        }
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
            onGalleryImageMaskPrepare={onGalleryImageMaskPrepare}
            onImageMaskApply={onImageMaskApply}
            onAudioUpload={(file, onProgress, signal) => onAudioUpload(input.id, file, onProgress, signal)}
            onVideoUpload={(file, onProgress, signal) => onVideoUpload(input.id, file, onProgress, signal)}
            onFileUpload={onFileUpload}
            onThreeDUpload={(file, onProgress, signal) => onThreeDUpload(input.id, file, onProgress, signal)}
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

function isTrackableJob(job: EngineJob) {
  return activeWorkflowProgressStatuses.has(job.status);
}

function isActiveWorkflowProgress(progress: JobProgress | null | undefined) {
  return Boolean(progress?.status && activeWorkflowProgressStatuses.has(progress.status));
}

function isTrackedRunActive(run: TrackedRun) {
  return activeWorkflowProgressStatuses.has(run.status);
}

function cancelableWorkflowRunCount(runs: TrackedRun[]) {
  return runs.filter(isTrackedRunActive).length;
}

function selectCurrentTrackedRun(runs: TrackedRun[]) {
  const active = runs.filter(isTrackedRunActive);
  return (
    active.find((run) => run.status === "running") ??
    active.find((run) => run.status === "queued") ??
    active.find((run) => run.status === "queued_pending_memory") ??
    active[0] ??
    null
  );
}

function trackedRunHandle(run: TrackedRun) {
  return run.type === "queue" ? run.queueId : run.jobId;
}

function trackedRunHandleSource(run: TrackedRun): WorkflowRuntimeHandleSource {
  return run.type === "queue" ? "workflow_run_queue" : "job";
}

function progressFromTrackedRun(run: TrackedRun | null, knownProgress: JobProgress | null = null): JobProgress | null {
  if (!run || !isTrackedRunActive(run)) return null;
  if (knownProgress && isActiveWorkflowProgress(knownProgress) && progressMatchesTrackedRun(knownProgress, run)) {
    return knownProgress;
  }
  return {
    job_id: trackedRunHandle(run),
    queue_id: run.type === "queue" ? run.queueId : run.queueId ?? null,
    status: run.status as JobProgress["status"],
    value: null,
    max: null,
    current_node: null,
    message: run.message,
  };
}

function progressMatchesTrackedRun(progress: JobProgress, run: TrackedRun) {
  const handle = trackedRunHandle(run);
  return (
    progress.job_id === handle
    || progress.queue_id === handle
    || (run.type === "job" && run.queueId ? progress.queue_id === run.queueId : false)
    || (run.type === "queue" && run.jobId ? progress.job_id === run.jobId : false)
  );
}

function trackedRunFromJob(job: EngineJob, existingClientId?: string): TrackedRun {
  const now = Date.now();
  const base = {
    clientId: existingClientId ?? `${job.queue_id ?? job.job_id}-${now}-${Math.random().toString(16).slice(2)}`,
    status: job.status,
    submittedAt: now,
    updatedAt: now,
    lastPolledAt: null,
    message: job.memory_status?.message ?? job.message ?? null,
  };
  if (job.queue_id && job.queue_id === job.job_id) {
    return { ...base, type: "queue", queueId: job.queue_id, jobId: null };
  }
  return { ...base, type: "job", jobId: job.job_id, queueId: job.queue_id ?? null };
}

function trackedRunFromProgress(run: TrackedRun, progress: JobProgress, lastPolledAt: number | null = run.lastPolledAt): TrackedRun {
  const now = Date.now();
  const queueId = progress.queue_id ?? (run.type === "queue" ? run.queueId : run.queueId ?? null);
  const base = {
    ...run,
    status: progress.status,
    updatedAt: now,
    lastPolledAt,
    message: progress.message,
  };
  if (queueId && progress.job_id !== queueId) {
    return { ...base, type: "job", jobId: progress.job_id, queueId };
  }
  if (run.type === "queue") {
    return { ...base, type: "queue", queueId: queueId ?? run.queueId, jobId: run.jobId ?? null };
  }
  return { ...base, type: "job", jobId: progress.job_id, queueId };
}

function trackedRunWithStatus(run: TrackedRun, status: string, message: string | null | undefined): TrackedRun {
  return {
    ...run,
    status,
    message: message ?? run.message,
    updatedAt: Date.now(),
  };
}

function isQueueOnlyTerminal(run: TrackedRun, progress: JobProgress) {
  return run.type === "queue" && progress.queue_id === run.queueId && progress.job_id === run.queueId;
}

function workflowCancelProgress(workflowId: string): JobProgress {
  return {
    job_id: `workflow-cancel-${workflowId}`,
    status: "canceled",
    value: null,
    max: null,
    current_node: null,
    message: "Workflow runs canceled.",
  };
}

function progressFromWorkflowRuntime(runtime: WorkflowTabRuntimeState | null): JobProgress | null {
  const status = runtime?.activeJobStatus;
  const jobId = runtime?.activeJobId ?? runtime?.queueId;
  if (!status || !jobId || !activeWorkflowProgressStatuses.has(status)) return null;
  return runtime.activeJobProgress ?? {
    job_id: jobId,
    status: status as JobProgress["status"],
    value: null,
    max: null,
    current_node: null,
    message: "Preparing workflow...",
  };
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

function progressFromSubmittedJob(job: EngineJob): JobProgress {
  return {
    job_id: job.job_id,
    queue_id: job.queue_id ?? null,
    status: job.status,
    value: null,
    max: null,
    current_node: null,
    message: job.memory_status?.message ?? job.message ?? "Preparing workflow...",
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

function progressMessage(progress: JobProgress | null, result: JobResult | null, memoryStatus: MemoryStatus | null = null) {
  if (memoryStatus) return memoryStatusDisplay(memoryStatus).message;
  if (progress?.status === "running") return progress.message ?? "Generating image...";
  if (progress?.status === "queued") return progress.message ?? "Preparing workflow...";
  if (progress?.status === "queued_pending_memory") return progress.message ?? "Waiting for memory.";
  if (progress?.status === "blocked_by_memory") return progress.message ?? "This workflow needs more memory.";
  if (progress?.status === "canceled") return "Run canceled.";
  if (result?.status === "completed") return "Result saved by the local workflow.";
  if (result?.status === "failed") return "The local engine could not finish this run.";
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
  return memoryStatusFallback(state).title;
}

function isWarmReusableMemoryState(state: string) {
  return state === "ready_warm_co_resident" || state === "ready_reusing_runner";
}

function isBlockingMemoryState(state: string) {
  return state === "blocked_by_memory" || state === "memory_cleanup_failed" || state.startsWith("blocked_");
}

function clampBatchCount(value: number) {
  if (!Number.isFinite(value)) return 1;
  return Math.min(99, Math.max(1, Math.round(value)));
}

interface MemoryStatusDisplay {
  title: string;
  message: string;
}

function memoryStatusDisplay(status: MemoryStatus): MemoryStatusDisplay {
  const fallback = memoryStatusFallback(status.state);
  const backendMessage = typeof status.message === "string" ? status.message.trim() : "";
  return {
    title: fallback.title,
    message: shouldUseMemoryStatusFallbackMessage(status.state, backendMessage) ? fallback.message : backendMessage,
  };
}

function memoryStatusFallback(state: string): MemoryStatusDisplay {
  if (state === "waiting_for_gpu") {
    return {
      title: "Waiting for the GPU",
      message: "Noofy will start this run when the GPU is available.",
    };
  }
  if (state === "waiting_for_active_workflow") {
    return {
      title: "Waiting for another run",
      message: "Noofy will start this workflow after the active run finishes.",
    };
  }
  if (state === "freeing_previous_models") {
    return {
      title: "Freeing previous models",
      message: "Noofy is unloading idle models so this workflow has enough room to start.",
    };
  }
  if (state === "unloading_previous_workflow") {
    return {
      title: "Unloading previous workflow",
      message: "Noofy is clearing the previous workflow before starting this one.",
    };
  }
  if (state === "freeing_memory" || state === "waiting_for_memory_release") {
    return {
      title: "Freeing memory",
      message: "Noofy asked the local engine to release memory and is waiting to confirm it.",
    };
  }
  if (state === "retrying_after_memory_cleanup") {
    return {
      title: "Retrying after memory cleanup",
      message: "Noofy freed memory and is trying this workflow one more time.",
    };
  }
  if (state === "memory_cleanup_failed") {
    return {
      title: "Memory cleanup did not finish",
      message: "Noofy tried to free memory, but could not confirm that enough was released.",
    };
  }
  if (state === "blocked_external_pressure") {
    return {
      title: "Other GPU work is using memory",
      message: "Another process is using GPU memory that Noofy cannot reclaim.",
    };
  }
  if (state === "blocked_exceeds_capacity") {
    return {
      title: "Workflow exceeds this machine's memory",
      message: "This workflow appears to need more RAM or VRAM than this machine can safely provide.",
    };
  }
  if (state === "blocked_unattributed_pressure") {
    return {
      title: "Memory is in use but not reclaimable",
      message: "Noofy sees memory pressure, but cannot safely attribute enough of it to memory it owns.",
    };
  }
  if (state === "blocked_by_memory") {
    return {
      title: "Memory unavailable",
      message: "Noofy cannot safely start this workflow with the memory available right now.",
    };
  }
  if (state === "ready_warm_co_resident" || state === "ready_reusing_runner") {
    return {
      title: "Models loaded",
      message: "The required models are already loaded, so the next run should start faster.",
    };
  }
  return {
    title: "Memory status",
    message: "Noofy is checking memory before starting this workflow.",
  };
}

function shouldUseMemoryStatusFallbackMessage(state: string, backendMessage: string) {
  if (!backendMessage) return true;
  const normalized = backendMessage.toLowerCase();
  if (state === "blocked_by_memory") return false;
  return (
    normalized === "not enough memory" ||
    normalized.includes("not enough memory is available") ||
    normalized.includes("needs more memory than noofy can safely use")
  );
}

function memoryStatusDeveloperDetails(job: EngineJob | null) {
  if (!job?.memory_status && !job?.memory_decision) return null;
  const details = {
    job_id: job?.job_id ?? null,
    queue_id: job?.queue_id ?? job?.memory_status?.queue_id ?? null,
    status: job?.status ?? null,
    memory_status: job?.memory_status ?? null,
    memory_decision: job?.memory_decision ?? null,
  };
  return JSON.stringify(details, null, 2);
}

function memoryNoticeClass(status: MemoryStatus) {
  if (status.state === "blocked_by_memory" || status.state === "memory_cleanup_failed" || status.state.startsWith("blocked_")) return "notice--error";
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
  memoryStatus: MemoryStatus | null;
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
  memoryStatus,
  missingModels,
  modelSummaryReady,
  validation,
  workflowStatus,
}: RunDisabledReasonInput): string {
  if (isBlockedByMemory && memoryStatus) return memoryStatusDisplay(memoryStatus).message;
  if (isBlockedByMemory) return "Noofy cannot safely start this workflow with the memory available right now.";
  if (isWaitingForMemory && memoryStatus) return memoryStatusDisplay(memoryStatus).message;
  if (isWaitingForMemory) return "Noofy is waiting for memory to free up.";
  if (isRunning) return "This workflow is already running.";
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
  "verification_failed",
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
  const activeDownload = Boolean(downloadJob && isModelDownloadActive(downloadJob.status));
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
      if (mediaOutputKind(image, "image") === "image" && image && typeof image === "object" && "view_url" in image && typeof image.view_url === "string") {
        urls.push(resolveBackendUrl(image.view_url, { includeToken: true }));
      }
    }
  }
  return urls;
}

function extractAudioOutputs(result: JobResult | null): OutputAudioMedia[] {
  return extractMediaItems(result, "audio")
    .map(({ item }) => normalizeAudioOutput(item))
    .filter((item): item is OutputAudioMedia => Boolean(item));
}

function extractVideoOutputs(result: JobResult | null): OutputVideoMedia[] {
  return extractMediaItems(result, "video")
    .map(({ item }) => normalizeVideoOutput(item))
    .filter((item): item is OutputVideoMedia => Boolean(item));
}

function extractFileOutputs(result: JobResult | null): OutputFileMedia[] {
  return extractMediaItems(result, "file")
    .map(({ item }) => normalizeFileOutput(item))
    .filter((item): item is OutputFileMedia => Boolean(item));
}

function extractThreeDOutputs(result: JobResult | null): OutputThreeDMedia[] {
  return extractMediaItems(result, "3d")
    .map(({ item }) => normalizeThreeDOutput(item))
    .filter((item): item is OutputThreeDMedia => Boolean(item));
}

function extractVideoOutputsByNodeId(result: JobResult | null): Map<string, OutputVideoMedia[]> {
  const map = new Map<string, OutputVideoMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "video")) {
    if (!nodeId) continue;
    const normalized = normalizeVideoOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

function extractFileOutputsByNodeId(result: JobResult | null): Map<string, OutputFileMedia[]> {
  const map = new Map<string, OutputFileMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "file")) {
    if (!nodeId) continue;
    const normalized = normalizeFileOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

function extractThreeDOutputsByNodeId(result: JobResult | null): Map<string, OutputThreeDMedia[]> {
  const map = new Map<string, OutputThreeDMedia[]>();
  for (const { item, nodeId } of extractMediaItems(result, "3d")) {
    if (!nodeId) continue;
    const normalized = normalizeThreeDOutput(item);
    if (normalized) map.set(nodeId, [...(map.get(nodeId) ?? []), normalized]);
  }
  return map;
}

function extractMediaItems(result: JobResult | null, kind: "audio" | "video" | "3d" | "file") {
  const outputs: Array<{ item: unknown; nodeId: string | null }> = [];
  if (!result) return outputs;
  for (const output of result.outputs) {
    const outputPayload = output.output;
    if (!outputPayload || typeof outputPayload !== "object") continue;
    const nodeIdKey = Object.keys(output).find((key) => key !== "output");
    const nodeId = typeof output.node_id === "string" ? output.node_id : nodeIdKey ?? null;
    for (const bucketName of ["images", "audio", "video", "videos", "gifs", "3d", "files", "text"]) {
      const items = (outputPayload as Record<string, unknown>)[bucketName];
      if (!Array.isArray(items)) continue;
      for (const item of items) {
        if (mediaOutputKind(item, bucketName) === kind) outputs.push({ item, nodeId });
      }
    }
  }
  return outputs;
}

function mediaOutputKind(item: unknown, bucketName: string): "image" | "audio" | "video" | "3d" | "file" {
  if (item && typeof item === "object") {
    const record = item as Record<string, unknown>;
    if (record.kind === "image" || record.kind === "audio" || record.kind === "video" || record.kind === "3d" || record.kind === "file") return record.kind;
    if (record.type === "image" || record.type === "audio" || record.type === "video" || record.type === "3d" || record.type === "file") return record.type;
    const mimeType =
      typeof record.mime_type === "string"
        ? record.mime_type.toLowerCase()
        : typeof record.content_type === "string"
          ? record.content_type.toLowerCase()
          : "";
    if (mimeType.startsWith("image/")) return "image";
    if (mimeType.startsWith("audio/")) return "audio";
    if (mimeType.startsWith("video/")) return "video";
    if (mimeType.startsWith("model/")) return "3d";
    if (mimeType && mimeType !== "application/octet-stream") return "file";
    const filename = typeof record.filename === "string" ? record.filename.toLowerCase() : "";
    if (/\.(mp4|mov|webm|mkv)$/.test(filename)) return "video";
    if (/\.(wav|mp3|flac|ogg|m4a)$/.test(filename)) return "audio";
    if (/\.(glb|gltf|obj|stl|fbx|ply|usdz|dae)$/.test(filename)) return "3d";
    if (/\.[a-z0-9][a-z0-9._-]*$/.test(filename)) return "file";
  }
  if (bucketName === "audio") return "audio";
  if (bucketName === "video" || bucketName === "videos") return "video";
  if (bucketName === "3d") return "3d";
  if (bucketName === "files" || bucketName === "text") return "file";
  return "image";
}

function normalizeVideoOutput(video: unknown): OutputVideoMedia | null {
  if (!video || typeof video !== "object") return null;
  const item = video as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-video");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    thumbnailUrl: typeof item.thumbnail_url === "string" ? resolveBackendUrl(item.thumbnail_url, { includeToken: true }) : null,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    durationSeconds: typeof item.duration_seconds === "number" ? item.duration_seconds : null,
    width: typeof item.width === "number" ? item.width : null,
    height: typeof item.height === "number" ? item.height : null,
    fps: typeof item.fps === "number" ? item.fps : null,
    size: typeof item.size === "number" ? item.size : null,
  };
}

function normalizeFileOutput(file: unknown): OutputFileMedia | null {
  if (!file || typeof file !== "object") return null;
  const item = file as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-file");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    extension: typeof item.extension === "string" ? item.extension : extensionFromFilename(filename),
    size: typeof item.size === "number" ? item.size : null,
  };
}

function normalizeThreeDOutput(model: unknown): OutputThreeDMedia | null {
  if (!model || typeof model !== "object") return null;
  const item = model as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-model.glb");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    thumbnailUrl: typeof item.thumbnail_url === "string" ? resolveBackendUrl(item.thumbnail_url, { includeToken: true }) : null,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    extension: typeof item.extension === "string" ? item.extension : extensionFromFilename(filename),
    size: typeof item.size === "number" ? item.size : null,
  };
}

function selectClassicPreviewMedia(
  controls: DashboardControlDef[],
  outputs: WorkflowOutputDef[],
  imagesByNodeId: Map<string, string[]>,
  audiosByNodeId: Map<string, OutputAudioMedia[]>,
  videosByNodeId: Map<string, OutputVideoMedia[]>,
  threeDsByNodeId: Map<string, OutputThreeDMedia[]>,
  filesByNodeId: Map<string, OutputFileMedia[]>,
  fallbackImages: string[],
  fallbackAudios: OutputAudioMedia[],
  fallbackVideos: OutputVideoMedia[],
  fallbackThreeDs: OutputThreeDMedia[],
  fallbackFiles: OutputFileMedia[],
) {
  const outputsById = new Map(outputs.map((output) => [output.id, output]));
  const declaredOutputs = controls
    .filter((control) => control.output_id)
    .map((control) => ({ controlId: control.id, output: outputsById.get(control.output_id!) }))
    .filter((item): item is { controlId: string; output: WorkflowOutputDef } => Boolean(item.output));
  for (const { controlId, output } of declaredOutputs) {
    const kind = output.kind ?? output.type;
    if (kind === "video" && videosByNodeId.get(output.node_id)?.[0]) return { kind: "video" as const, media: videosByNodeId.get(output.node_id)![0], controlId };
    if (kind === "image" && imagesByNodeId.get(output.node_id)?.[0]) return { kind: "image" as const, url: imagesByNodeId.get(output.node_id)![0], controlId };
    if (kind === "audio" && audiosByNodeId.get(output.node_id)?.[0]) return { kind: "audio" as const, media: audiosByNodeId.get(output.node_id)![0], controlId };
    if (kind === "3d" && threeDsByNodeId.get(output.node_id)?.[0]) return { kind: "3d" as const, media: threeDsByNodeId.get(output.node_id)![0], controlId };
    if (kind === "file" && filesByNodeId.get(output.node_id)?.[0]) return { kind: "file" as const, media: filesByNodeId.get(output.node_id)![0], controlId };
  }
  for (const output of outputs) {
    const kind = output.kind ?? output.type;
    if (kind === "video" && videosByNodeId.get(output.node_id)?.[0]) return { kind: "video" as const, media: videosByNodeId.get(output.node_id)![0] };
    if (kind === "image" && imagesByNodeId.get(output.node_id)?.[0]) return { kind: "image" as const, url: imagesByNodeId.get(output.node_id)![0] };
    if (kind === "audio" && audiosByNodeId.get(output.node_id)?.[0]) return { kind: "audio" as const, media: audiosByNodeId.get(output.node_id)![0] };
    if (kind === "3d" && threeDsByNodeId.get(output.node_id)?.[0]) return { kind: "3d" as const, media: threeDsByNodeId.get(output.node_id)![0] };
    if (kind === "file" && filesByNodeId.get(output.node_id)?.[0]) return { kind: "file" as const, media: filesByNodeId.get(output.node_id)![0] };
  }
  if (fallbackVideos[0]) return { kind: "video" as const, media: fallbackVideos[0] };
  if (fallbackImages[0]) return { kind: "image" as const, url: fallbackImages[0] };
  if (fallbackAudios[0]) return { kind: "audio" as const, media: fallbackAudios[0] };
  if (fallbackThreeDs[0]) return { kind: "3d" as const, media: fallbackThreeDs[0] };
  if (fallbackFiles[0]) return { kind: "file" as const, media: fallbackFiles[0] };
  return null;
}

function videoOutputMetaLabel(video: OutputVideoMedia): string {
  return videoMetadataLabel(null, video.mimeType, video.size, video.durationSeconds, video.width, video.height, video.fps, "Video output");
}

function normalizeAudioOutput(audio: unknown): OutputAudioMedia | null {
  if (!audio || typeof audio !== "object") return null;
  const item = audio as Record<string, unknown>;
  const rawUrl = typeof item.view_url === "string" ? item.view_url : typeof item.url === "string" ? item.url : null;
  if (!rawUrl) return null;
  const filename = typeof item.filename === "string" && item.filename ? item.filename : filenameFromMediaUrl(rawUrl, "noofy-audio");
  return {
    url: resolveBackendUrl(rawUrl, { includeToken: true }),
    filename,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    durationSeconds: typeof item.duration_seconds === "number" ? item.duration_seconds : null,
    size: typeof item.size === "number" ? item.size : null,
  };
}

function filenameFromMediaUrl(rawUrl: string, fallback: string): string {
  try {
    const url = new URL(rawUrl, window.location.href);
    return url.searchParams.get("filename") || fallback;
  } catch {
    return fallback;
  }
}

function audioOutputMetaLabel(audio: OutputAudioMedia): string {
  return audioMetadataLabel(null, audio.mimeType, audio.size, audio.durationSeconds, "Audio output");
}

function fileOutputMetaLabel(file: OutputFileMedia): string {
  return fileMetadataLabel(file.extension, file.mimeType, file.size, "File output");
}

function downloadMediaDirect(mediaUrl: string, filename: string) {
  const link = document.createElement("a");
  const url = new URL(mediaUrl, window.location.href);
  url.searchParams.set("download", "true");
  link.href = url.toString();
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function failedGallerySaveRequest(jobId: string, controlId: string, error: unknown): GallerySaveRequest {
  const message = error instanceof Error ? error.message : "Gallery save failed. Retry this output.";
  const unavailable = /(?:not|no longer) available|unavailable/i.test(message);
  return {
    job_id: jobId,
    control_id: controlId,
    status: unavailable ? "unavailable" : "failed",
    message,
    bytes_copied: 0,
    total_bytes: null,
    item_ids: [],
    updated_at: new Date().toISOString(),
  };
}

function extensionFromFilename(filename: string): string | null {
  const parts = filename.split(".");
  return parts.length > 1 ? `.${parts[parts.length - 1].toLowerCase()}` : null;
}

function comparisonImageAssetIdForRun(
  packageData: WorkflowPackageResponse | null,
  controls: DashboardControlDef[],
  inputValues: Record<string, unknown>,
): string | null {
  for (const control of controls) {
    if (!comparisonImageInputControlTypes.has(control.type) || !control.input_id) continue;
    const value = inputValues[control.input_id];
    if (isDashboardImageAssetReference(value)) return value;
  }

  for (const input of packageData?.inputs ?? []) {
    if (!comparisonImageInputControlTypes.has(input.control)) continue;
    const value = inputValues[input.id];
    if (isDashboardImageAssetReference(value)) return value;
  }

  return null;
}

function isDashboardImageAssetReference(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    !value.includes("/") &&
    !value.includes("\\") &&
    /\.(?:png|jpg|jpeg|webp|gif)$/i.test(value)
  );
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
  inputValues: Record<string, unknown>,
): DashboardSchema | null {
  const widgets: DashboardWidget[] = [];
  const referencedInputIds = new Set<string>();
  const groupedControlIds = groupedControlIdSet(groups);
  const controlTypeById = new Map(controls.map((control) => [control.id, control.type]));

  for (const control of controls) {
    const layout = groupedControlIds.has(control.id)
      ? undefined
      : layoutForBuilderControl(control, layoutOverrides[control.id]);

    if (control.type === "note") {
      const input = control.input_id ? inputIndex.get(control.input_id) : undefined;
      if (input) referencedInputIds.add(input.id);
      const defaultValue = input ? builderDefaultValueForInput(input, inputValues) : null;
      widgets.push({
        id: control.id,
        valueId: input?.id ?? `note:${control.id}`,
        binding: input
          ? { nodeId: input.binding.node_id, inputName: input.binding.input_name }
          : { nodeId: "", inputName: "" },
        widgetType: "note",
        title: control.label,
        description: control.description ?? "",
        defaultValue,
        ...(input ? { hasExecutableBinding: true } : {}),
        layout,
      });
      continue;
    }

    if (control.input_id) {
      const input = inputIndex.get(control.input_id);
      if (!input) continue;
      referencedInputIds.add(input.id);
      widgets.push({
        id: control.id,
        valueId: input.id,
        binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
        widgetType: toBuilderWidgetType(control.type),
        title: control.label,
        description: control.description ?? "",
        defaultValue: builderDefaultValueForInput(input, inputValues),
        min: numberValidation(input.validation.min),
        max: numberValidation(input.validation.max),
        step: numberValidation(input.validation.step),
        options: stringArrayValidation(input.validation.options),
        acceptedExtensions: stringArrayValidation(input.validation.accepted_extensions),
        acceptedMimeTypes: stringArrayValidation(input.validation.accepted_mime_types),
        layout,
      });
      continue;
    }

    if (control.output_id) {
      const output = outputIndex.get(control.output_id);
      if (!output) continue;
      const outputKind = output.kind ?? output.type;
      widgets.push({
        id: control.id,
        valueId: output.id,
        binding: { nodeId: output.node_id, inputName: "" },
        widgetType: outputKind === "audio" ? "display_audio" : outputKind === "video" ? "display_video" : outputKind === "3d" ? "display_3d" : outputKind === "file" ? "display_file" : "display_image",
        title: control.label,
        description: control.description ?? "",
        defaultValue: null,
        layout,
      });
    }
  }

  if (widgets.length === 0) return null;
  const hiddenWidgets = Array.from(inputIndex.values())
    .filter((input) => !referencedInputIds.has(input.id))
    .map((input) => hiddenBuilderWidgetForInput(input, inputValues))
    .filter((widget): widget is DashboardWidget => Boolean(widget));

  return {
    version: 1,
    workflowId,
    workflowName,
    widgets,
    hiddenWidgets: hiddenWidgets.length > 0 ? hiddenWidgets : undefined,
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

function hiddenBuilderWidgetForInput(
  input: WorkflowInputDef,
  inputValues: Record<string, unknown>,
): DashboardWidget | null {
  const widgetType = inputWidgetTypeForBuilder(input.control);
  if (!widgetType) return null;
  const widget: DashboardWidget = {
    id: input.id,
    valueId: input.id,
    binding: { nodeId: input.binding.node_id, inputName: input.binding.input_name },
    widgetType,
    title: input.label,
    description: "",
    defaultValue: builderDefaultValueForInput(input, inputValues),
    min: numberValidation(input.validation.min),
    max: numberValidation(input.validation.max),
    step: numberValidation(input.validation.step),
    options: stringArrayValidation(input.validation.options),
    acceptedExtensions: stringArrayValidation(input.validation.accepted_extensions),
    acceptedMimeTypes: stringArrayValidation(input.validation.accepted_mime_types),
  };
  if (widget.widgetType === "note") widget.hasExecutableBinding = true;
  return canPreserveWidgetAsHiddenInput(widget) ? widget : null;
}

function builderDefaultValueForInput(
  input: WorkflowInputDef,
  inputValues: Record<string, unknown>,
): unknown {
  return Object.prototype.hasOwnProperty.call(inputValues, input.id)
    ? inputValues[input.id]
    : input.default;
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
  return inputWidgetTypeForBuilder(type) ?? "string_field";
}

function inputWidgetTypeForBuilder(type: string): WidgetType | null {
  const knownTypes = new Set<WidgetType>([
    "slider",
    "int_field",
    "string_field",
    "textarea",
    "note",
    "toggle",
    "load_image",
    "load_image_mask",
    "load_audio",
    "load_video",
    "load_file",
    "load_3d",
    "display_image",
    "display_audio",
    "display_video",
    "display_file",
    "display_3d",
    "seed_widget",
    "lora_loader",
    "select",
  ]);
  return knownTypes.has(type as WidgetType) ? (type as WidgetType) : null;
}

function numberValidation(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function stringArrayValidation(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const options = value.filter((option): option is string => typeof option === "string" && option.length > 0);
  return options.length > 0 ? options : undefined;
}
