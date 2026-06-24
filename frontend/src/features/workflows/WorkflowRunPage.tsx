import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Download,
  File as FileIcon,
  Image,
  Loader2,
  RotateCcw,
} from "lucide-react";

import {
  cancelWorkflowActiveAndQueuedRuns,
  copyGalleryImageToDashboardAsset,
  exportWorkflowComfyJsonUrl,
  exportWorkflowUrl,
  fetchApiKeySettings,
  fetchJobLogs,
  fetchJobProgress,
  fetchJobResult,
  fetchLogs,
  fetchWorkflowActiveAndQueuedRuns,
  fetchWorkflowModelSummary,
  fetchWorkflowPackage,
  fetchWorkflowStatus,
  isEngineJob,
  isApiError,
  resetDashboardCustomization,
  runWorkflow,
  saveDashboard,
  uploadDashboardAsset,
  uploadDashboardAudioAsset,
  uploadDashboardFileAsset,
  uploadDashboardImageMaskAsset,
  uploadDashboardVideoAsset,
  uploadDashboardThreeDAsset,
  validateWorkflow,
  type DashboardControlDef,
  type DiagnosticEvent,
  type EngineJob,
  type JobProgress,
  type JobResult,
  type MemoryRequirement,
  type RunUserFixableError,
  type WorkflowInputDef,
  type WorkflowOutputDef,
  type WorkflowPackageResponse,
  type WorkflowStatusResponse,
  type WorkflowValidationResult,
  type UploadProgress,
} from "../../lib/api/noofyApi";
import type { DashboardSchema } from "../dashboard-builder/dashboardBuilderContent";
import type { GridItemLayout } from "../../lib/gridLayout";
import { useAppPreferences } from "../../lib/useAppPreferences";
import { useWorkflowUserState } from "../../lib/useWorkflowUserState";
import { workflowDisplayName } from "../../lib/workflowNames";
import {
  type OutputAudioMedia,
  type OutputFileMedia,
  type OutputThreeDMedia,
  type OutputVideoMedia,
} from "./media";
import { ThreeDViewer } from "../three-d/ThreeDViewer";
import { AppLayout, type AppRouteId } from "../app/AppLayout";
import { useRuntimeStatus } from "../app/RuntimeStatusProvider";
import { useOptionalWorkflowTabs } from "../app/WorkflowTabs";
import { loadWorkflowRunHandle, vanishedRunRecoveryMessage, type WorkflowRunHandleSnapshot } from "../app/sessionRestore";
import { CanvasDashboardView, type CanvasActionBarPosition } from "./CanvasDashboardView";
import { WorkflowActionBar, type WorkflowActionBarRunState } from "./WorkflowActionBar";
import { GallerySaveAction } from "./GallerySaveAction";
import { CivitaiLoraBrowserModal } from "./CivitaiLoraBrowserModal";
import { ImageComparisonSlider } from "./ImageComparisonSlider";
import { RetainedImage } from "./RetainedImage";
import { WorkflowExportDialog } from "./WorkflowExportDialog";
import { WorkflowDefaultAssetProvider, type LoraBrowserControlProps } from "./DashboardInputControl";
import { nextSeedValue, seedModeFromValidation, type SeedMode } from "../../lib/seedControl";
import { topLevelDashboardControlItems } from "./dashboardTopLevelItems";
import type { WorkflowExportReviewModel } from "../../lib/workflowExport";
import {
  cachedWorkflowRunPageState,
  invalidateWorkflowRunPageCache,
  storeWorkflowRunPageState,
  type WorkflowRunPageCachedState,
} from "./workflowRunPageCache";
import {
  BatchFailureSummary,
  DashboardSetupRequired,
  focusDashboardControl,
  formatFailureLogsReport,
  formatFailureReport,
  formatInputErrorReport,
  packageNeedsDashboardSetup,
  RunPreparationDialog,
  splitDiagnosticLogs,
  WorkflowCancelConfirmation,
  WorkflowFailureDialog,
  WorkflowInputErrorDialog,
  WorkflowMissingPanel,
  WorkflowRefreshRequiredDialog,
} from "./WorkflowRunDialogs";
import { DashboardInputControls, FallbackInputs } from "./WorkflowClassicInputs";
import {
  actionBarPositionFromDashboard,
  buildDashboardSchemaForEditing,
  dashboardSavePayloadWithActionBarPosition,
  dashboardSavePayloadWithTitle,
  dashboardUserStateVersion,
  updatePackageActionBarPosition,
  updatePackageDashboardTitle,
} from "./workflowDashboardRunEditing";
import {
  activeRequiredModelSummary,
  activeWorkflowValidation,
  normalizedLoraInputValues,
  requiredModelDownloadSelections,
  WorkflowRequiredModelsModal,
} from "./workflowModelRequirements";
import {
  clampBatchCount,
  isBlockingMemoryState,
  isMemoryFailureCode,
  isSilentQueuedMemoryState,
  isWarmReusableMemoryState,
  MEMORY_FAILURE_MESSAGE,
  MemoryLoadedPill,
  memoryNoticeClass,
  memoryStatusDeveloperDetails,
  memoryStatusDisplay,
  memoryStatusTitle,
  progressMessage,
  workflowRunDisabledReason,
} from "./workflowMemoryStatus";
import {
  audioOutputMetaLabel,
  defaultValueForWorkflowInput,
  downloadMediaDirect,
  extractAudioOutputs,
  extractAudioOutputsByNodeId,
  extractFileOutputs,
  extractFileOutputsByNodeId,
  extractImageUrls,
  extractImageUrlsByNodeId,
  extractTextOutputsByNodeId,
  extractThreeDOutputs,
  extractThreeDOutputsByNodeId,
  extractVideoOutputs,
  extractVideoOutputsByNodeId,
  fileOutputMetaLabel,
  isDashboardOutputControl,
  isReusableRecoveredResult,
  selectClassicPreviewMedia,
  shouldRetryRecoveredResult,
  videoOutputMetaLabel,
  wait,
} from "./workflowRunOutputs";
import { useGallerySaveState } from "./useGallerySaveState";
import {
  activeWorkflowProgressStatuses,
  cancelableWorkflowRunCount,
  isActiveWorkflowProgress,
  isQueueOnlyTerminal,
  isTrackableJob,
  isTrackedRunActive,
  optimisticJobId,
  optimisticProgress,
  progressFromRecoveredResult,
  progressFromSubmittedJob,
  progressFromTrackedRun,
  progressFromWorkflowRuntime,
  progressMatchesTrackedRun,
  selectCurrentTrackedRun,
  terminalProgressFromResult,
  terminalProgressFromStoredRunHandle,
  terminalProgressFromWorkflowRuntime,
  terminalStatuses,
  trackedRunFromJob,
  trackedRunFromProgress,
  trackedRunFromResult,
  trackedRunHandle,
  trackedRunHandleSource,
  trackedRunWithStatus,
  workflowCancelProgress,
  workflowHandleSource,
} from "./workflowRunTracking";
import {
  firstRunUserFixableError,
  runPreparationDialogFromStatus,
  runPreparationDialogFromValidation,
  shouldShowRunPreparationDialog,
  workflowInstallStatus,
  workflowValidationErrorMessage,
} from "./workflowPreparationStatus";
import {
  cacheRunPageResult,
  storeRunPageResultSnapshot,
} from "./workflowRunResultCache";
import { useWorkflowRunnerLease } from "./useWorkflowRunnerLease";
import { useWorkflowModelActions } from "./useWorkflowModelActions";
import type {
  FailedTrackedRun,
  LoraBrowserDialogState,
  RunFailureDialogState,
  RunInputErrorDialogState,
  RunPreparationDialogState,
  StoredLivePreview,
  TrackedRun,
  WorkflowCancelConfirmationState,
} from "./workflowRunStateTypes";
import { useRunComparisonInputImage } from "./useRunComparisonInputImage";

export { splitDiagnosticLogs };

interface WorkflowRunPageProps {
  workflowId: string;
  onBack: () => void;
  onWorkflowNameChange?: (workflowName: string) => void;
  onMissingWorkflow?: (workflowId: string) => void;
  onEditWidgets?: (schema: DashboardSchema) => void;
  onConfigureDashboard?: (workflowId?: string, workflowName?: string) => void;
  onNavigate: (route: AppRouteId) => void;
}

type RunPageState = WorkflowRunPageCachedState;

const initialState: RunPageState = {
  firstLoadedWorkflowId: null,
  workflowStatus: null,
  modelSummary: null,
  packageData: null,
  apiKeySettings: null,
  validation: null,
  modelSummaryLoading: false,
  validationLoading: false,
  job: null,
  progress: null,
  result: null,
  error: null,
  packageLoadError: null,
  packageLoadErrorStatus: null,
};

const logLimit = 200;
const runtimeResultRecoveryRetryMs = 1000;
const runtimeResultRecoveryMaxAttempts = 90;

export function WorkflowRunPage({
  workflowId,
  onBack,
  onWorkflowNameChange,
  onMissingWorkflow,
  onEditWidgets,
  onConfigureDashboard,
  onNavigate,
}: WorkflowRunPageProps) {
  const [state, setState] = useState<RunPageState>(() => cachedWorkflowRunPageState(workflowId, initialState));
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [failureDialog, setFailureDialog] = useState<RunFailureDialogState | null>(null);
  const [inputErrorDialog, setInputErrorDialog] = useState<RunInputErrorDialogState | null>(null);
  const [failedTrackedRuns, setFailedTrackedRuns] = useState<FailedTrackedRun[]>([]);
  const [failedRunSummaryOpen, setFailedRunSummaryOpen] = useState(false);
  const [workflowCancelConfirmation, setWorkflowCancelConfirmation] = useState<WorkflowCancelConfirmationState | null>(null);
  const [batchCount, setBatchCount] = useState(1);
  const [trackedRuns, setTrackedRuns] = useState<TrackedRun[]>([]);
  const [runPreparationDialog, setRunPreparationDialog] = useState<RunPreparationDialogState | null>(null);
  const [loraBrowserDialog, setLoraBrowserDialog] = useState<LoraBrowserDialogState | null>(null);
  const [exportDialog, setExportDialog] = useState<{ extension: ".noofy" | ".json"; url: string } | null>(null);
  const [requiredModelsModalOpen, setRequiredModelsModalOpen] = useState(false);
  const [downloadedLoraOptions, setDownloadedLoraOptions] = useState<Record<string, string[]>>({});
  const [draftLayoutOverrides, setDraftLayoutOverrides] = useState<Record<string, GridItemLayout> | null>(null);
  const [draftActionBarPosition, setDraftActionBarPosition] = useState<CanvasActionBarPosition | null>(null);
  const [draftActionBarTouched, setDraftActionBarTouched] = useState(false);
  const {
    comparisonInputImageUrl,
    clearRunComparisonInputSource,
    resolveRunComparisonInputSource,
  } = useRunComparisonInputImage(workflowId);
  const [livePreview, setLivePreview] = useState<StoredLivePreview | null>(null);
  const trackedRunsRef = useRef<TrackedRun[]>([]);
  const livePreviewRef = useRef<StoredLivePreview | null>(null);
  const trackedRunPollInFlightRef = useRef<Set<string>>(new Set());
  const runtimeResultRecoveryInFlightRef = useRef<string | null>(null);
  const runSubmissionInFlightCountRef = useRef(0);
  const dashboardSetupRouteRequestedRef = useRef<string | null>(null);
  const requirementsLoadSequenceRef = useRef(0);
  const missingWorkflowNotifiedRef = useRef<string | null>(null);
  const activeWorkflowIdRef = useRef(workflowId);
  activeWorkflowIdRef.current = workflowId;

  useEffect(() => {
    trackedRunsRef.current = trackedRuns;
  }, [trackedRuns]);

  useEffect(() => {
    livePreviewRef.current = livePreview;
  }, [livePreview]);

  useEffect(() => {
    if (state.firstLoadedWorkflowId === workflowId) {
      storeWorkflowRunPageState(workflowId, state);
    }
  }, [state, workflowId]);

  const { viewMode, setViewMode } = useAppPreferences();
  const runtimeStatus = useRuntimeStatus();
  const workflowTabs = useOptionalWorkflowTabs();
  const workflowRuntime = workflowTabs?.runtimeByWorkflowId[workflowId] ?? null;
  const [storedWorkflowRunHandle, setStoredWorkflowRunHandle] = useState<WorkflowRunHandleSnapshot | null>(
    () => loadWorkflowRunHandle(workflowId),
  );
  useEffect(() => {
    setStoredWorkflowRunHandle(loadWorkflowRunHandle(workflowId));
  }, [
    workflowId,
    workflowRuntime?.activeJobId,
    workflowRuntime?.activeJobProgress?.job_id,
    workflowRuntime?.activeJobProgress?.status,
    workflowRuntime?.activeJobStatus,
    workflowRuntime?.activeJobUpdatedAt,
    workflowRuntime?.queueId,
  ]);
  const runtimeProgress = progressFromWorkflowRuntime(workflowRuntime);
  const terminalRuntimeProgress = terminalProgressFromWorkflowRuntime(workflowRuntime)
    ?? terminalProgressFromStoredRunHandle(storedWorkflowRunHandle);
  const remainingTrackedRunCount = trackedRuns.filter(isTrackedRunActive).length;
  const currentTrackedRun = selectCurrentTrackedRun(trackedRuns);
  // Shared runtime polling continues while this page is unmounted. A terminal
  // update must win outright; active runtime progress is authoritative over
  // the older page cache when returning here.
  const displayedProgress = terminalRuntimeProgress
    ?? progressFromTrackedRun(currentTrackedRun, runtimeProgress ?? state.progress)
    ?? runtimeProgress
    ?? state.progress;
  const hasTerminalProgress = Boolean(displayedProgress?.status && terminalStatuses.has(displayedProgress.status));
  const activeJobStatus = hasTerminalProgress ? null : state.job?.status;
  const activeRuntimeJobId = currentTrackedRun ? trackedRunHandle(currentTrackedRun) : workflowRuntime?.activeJobId ?? workflowRuntime?.queueId ?? null;
  const activeMemoryStatus = remainingTrackedRunCount === 0 && hasTerminalProgress
    ? null
    : displayedProgress?.memory_status ?? state.job?.memory_status ?? null;
  const warmReusableMemoryReady = Boolean(activeMemoryStatus && isWarmReusableMemoryState(activeMemoryStatus.state));
  const isRunning = !warmReusableMemoryReady && (isSubmittingRun || remainingTrackedRunCount > 0 || isActiveWorkflowProgress(displayedProgress));
  const isWaitingForMemory = activeJobStatus === "queued_pending_memory" || displayedProgress?.status === "queued_pending_memory";
  const isBlockedByMemory = activeJobStatus === "blocked_by_memory";
  const activeProgressJobId =
    displayedProgress &&
    isActiveWorkflowProgress(displayedProgress) &&
    displayedProgress.job_id !== optimisticJobId
      ? displayedProgress.job_id
      : null;
  const activeCancelableRunHandle = activeRuntimeJobId ?? activeProgressJobId;
  const outputImages = useMemo(() => extractImageUrls(state.result), [state.result]);
  const outputAudios = useMemo(() => extractAudioOutputs(state.result), [state.result]);
  const outputVideos = useMemo(() => extractVideoOutputs(state.result), [state.result]);
  const outputFiles = useMemo(() => extractFileOutputs(state.result), [state.result]);
  const outputThreeDs = useMemo(() => extractThreeDOutputs(state.result), [state.result]);

  // A workflow prop change renders before the reset effect below runs. Keep
  // the previous workflow's package and readiness data out of reconciliation
  // and UI.
  const loadedWorkflowStateMatches = state.firstLoadedWorkflowId === workflowId;
  const packageDataForWorkflow =
    loadedWorkflowStateMatches ? state.packageData : null;
  const workflowStatusForWorkflow =
    loadedWorkflowStateMatches ? state.workflowStatus : null;
  const modelSummaryForWorkflow =
    loadedWorkflowStateMatches ? state.modelSummary : null;
  const validationForWorkflow =
    loadedWorkflowStateMatches ? state.validation : null;
  const modelSummaryLoadingForWorkflow =
    loadedWorkflowStateMatches && state.modelSummaryLoading;
  const validationLoadingForWorkflow =
    loadedWorkflowStateMatches && state.validationLoading;

  // Build input index from package data.
  const inputIndex = useMemo<Map<string, WorkflowInputDef>>(() => {
    const map = new Map<string, WorkflowInputDef>();
    for (const input of packageDataForWorkflow?.inputs ?? []) {
      map.set(input.id, input);
    }
    return map;
  }, [packageDataForWorkflow]);

  // Build output index.
  const outputIndex = useMemo<Map<string, WorkflowOutputDef>>(() => {
    const map = new Map<string, WorkflowOutputDef>();
    for (const output of packageDataForWorkflow?.outputs ?? []) {
      map.set(output.id, output);
    }
    return map;
  }, [packageDataForWorkflow]);

  // Collect creator defaults from package data.
  const packageDefaults = useMemo<Record<string, unknown>>(() => {
    const defaults: Record<string, unknown> = {};
    for (const input of packageDataForWorkflow?.inputs ?? []) {
      defaults[input.id] = defaultValueForWorkflowInput(input);
    }
    return defaults;
  }, [packageDataForWorkflow]);

  const allControls = useMemo(
    () => packageDataForWorkflow?.dashboard?.sections.flatMap((section) => section.controls) ?? [],
    [packageDataForWorkflow],
  );
  const shouldWaitForRecoveredOutputPayload = !packageDataForWorkflow || allControls.some(isDashboardOutputControl);
  const allGroups = useMemo(
    () => packageDataForWorkflow?.dashboard?.sections.flatMap((section) => section.groups ?? []) ?? [],
    [packageDataForWorkflow],
  );
  const topLevelItems = useMemo(
    () =>
      packageDataForWorkflow?.dashboard?.sections.flatMap((section) =>
        topLevelDashboardControlItems(section.controls, section.groups ?? []),
      ) ?? [],
    [packageDataForWorkflow],
  );
  const dashboardVersion = useMemo(
    () => dashboardUserStateVersion(packageDataForWorkflow),
    [packageDataForWorkflow],
  );
  const dashboardControlIds = useMemo(() => allControls.map((control) => control.id), [allControls]);
  const dashboardLayoutIds = useMemo(() => topLevelItems.map((item) => item.id), [topLevelItems]);

  const {
    loaded: userStateLoaded,
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

  // Seed "control after generate" behavior. The default per seed input comes
  // from the saved dashboard (validation.seed_mode); the runner can override it
  // for the session via the dropdown on the seed control.
  const seedDefaultModes = useMemo<Record<string, SeedMode>>(() => {
    const modes: Record<string, SeedMode> = {};
    for (const input of inputIndex.values()) {
      if (input.control === "seed_widget") {
        modes[input.id] = seedModeFromValidation(input.validation);
      }
    }
    return modes;
  }, [inputIndex]);
  const [seedModeOverrides, setSeedModeOverrides] = useState<Record<string, SeedMode>>({});
  const seedModes = useMemo(
    () => ({ ...seedDefaultModes, ...seedModeOverrides }),
    [seedDefaultModes, seedModeOverrides],
  );
  function handleSeedModeChange(inputId: string, mode: SeedMode) {
    setSeedModeOverrides((current) => ({ ...current, [inputId]: mode }));
  }

  const submittedInputValues = useMemo(
    () => normalizedLoraInputValues(packageDataForWorkflow, inputValues),
    [packageDataForWorkflow, inputValues],
  );
  const activeModelSummary = useMemo(
    () => activeRequiredModelSummary(modelSummaryForWorkflow, packageDataForWorkflow, submittedInputValues),
    [modelSummaryForWorkflow, packageDataForWorkflow, submittedInputValues],
  );
  const activeValidation = useMemo(
    () => activeWorkflowValidation(validationForWorkflow, packageDataForWorkflow, submittedInputValues),
    [validationForWorkflow, packageDataForWorkflow, submittedInputValues],
  );
  const {
    modelDownloadJob,
    modelDownloadError,
    modelDownloadStarting,
    modelVerificationJob,
    modelVerificationError,
    downloadRequiredModels: handleDownloadRequiredModels,
    cancelRequiredModelDownload: handleCancelModelDownload,
    startLocalModelVerification,
  } = useWorkflowModelActions({
    workflowId,
    activeModelSummary,
    requiredModelsModalOpen,
    loadRequirements,
    onModelSummary: (modelSummary) => {
      setState((current) => ({ ...current, modelSummary }));
    },
  });

  const outputImagesByNodeId = useMemo<Map<string, string[]>>(
    () => extractImageUrlsByNodeId(state.result),
    [state.result],
  );

  const outputAudiosByNodeId = useMemo<Map<string, OutputAudioMedia[]>>(
    () => extractAudioOutputsByNodeId(state.result),
    [state.result],
  );
  const outputTextsByNodeId = useMemo<Map<string, string[]>>(
    () => extractTextOutputsByNodeId(state.result),
    [state.result],
  );

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
    () => selectClassicPreviewMedia(allControls, packageDataForWorkflow?.outputs ?? [], outputImagesByNodeId, outputAudiosByNodeId, outputVideosByNodeId, outputThreeDsByNodeId, outputFilesByNodeId, outputImages, outputAudios, outputVideos, outputThreeDs, outputFiles),
    [allControls, outputAudios, outputAudiosByNodeId, outputFiles, outputFilesByNodeId, outputImages, outputImagesByNodeId, outputThreeDs, outputThreeDsByNodeId, outputVideos, outputVideosByNodeId, packageDataForWorkflow?.outputs],
  );
  const activeLivePreview = livePreview?.data_url ? livePreview : null;
  const classicVisualImageUrl = activeLivePreview?.data_url
    ?? (classicPreviewMedia?.kind === "image" ? classicPreviewMedia.url : null);
  const {
    gallerySaveByControlId,
    saveOutputToGallery: handleSaveOutputToGallery,
    cancelOutputGallerySave: handleCancelOutputGallerySave,
  } = useGallerySaveState(state.result);

  async function loadRequirements() {
    const targetWorkflowId = workflowId;
    const loadSequence = ++requirementsLoadSequenceRef.current;
    setState((current) => {
      const hasCurrentWorkflowData = current.firstLoadedWorkflowId === targetWorkflowId;
      return {
        ...current,
        workflowStatus: hasCurrentWorkflowData ? current.workflowStatus : null,
        modelSummary: hasCurrentWorkflowData ? current.modelSummary : null,
        packageData: hasCurrentWorkflowData ? current.packageData : null,
        apiKeySettings: hasCurrentWorkflowData ? current.apiKeySettings : null,
        validation: hasCurrentWorkflowData ? current.validation : null,
        modelSummaryLoading: !hasCurrentWorkflowData || current.modelSummary === null,
        validationLoading: !hasCurrentWorkflowData || current.validation === null,
        error: null,
        packageLoadError: null,
        packageLoadErrorStatus: null,
      };
    });

    const isCurrentLoad = () =>
      loadSequence === requirementsLoadSequenceRef.current &&
      activeWorkflowIdRef.current === targetWorkflowId;

    const workflowStatusPromise = fetchWorkflowStatus(targetWorkflowId).catch(() => null);
    const packagePromise = fetchWorkflowPackage(targetWorkflowId)
      .then((packageData) => ({ packageData, error: null, status: null }))
      .catch((error: unknown) => ({
        packageData: null,
        error: error instanceof Error ? error.message : String(error),
        status: isApiError(error) ? error.status : null,
      }));
    const modelSummaryPromise = fetchWorkflowModelSummary(targetWorkflowId).catch(() => null);
    const apiKeySettingsPromise = fetchApiKeySettings().catch(() => null);
    const validationPromise = validateWorkflow(targetWorkflowId)
      .then((validation): { validation: WorkflowValidationResult | null; error: unknown | null } => ({
        validation,
        error: null,
      }))
      .catch((error: unknown): { validation: null; error: unknown } => ({ validation: null, error }));

    const [workflowStatus, packageResult] = await Promise.all([workflowStatusPromise, packagePromise]);
    if (!isCurrentLoad()) return;
    setState((current) => {
      const next = {
        ...current,
        firstLoadedWorkflowId: targetWorkflowId,
        workflowStatus,
        packageData:
          packageResult.packageData ??
          (current.firstLoadedWorkflowId === targetWorkflowId ? current.packageData : null),
        packageLoadError: packageResult.error,
        packageLoadErrorStatus: packageResult.error ? packageResult.status : null,
      };
      storeWorkflowRunPageState(targetWorkflowId, next);
      return next;
    });

    await Promise.allSettled([
      modelSummaryPromise.then((modelSummary) => {
        if (!isCurrentLoad()) return;
        setState((current) => ({
          ...current,
          modelSummary,
          modelSummaryLoading: false,
        }));
      }),
      apiKeySettingsPromise.then((apiKeySettings) => {
        if (!isCurrentLoad()) return;
        setState((current) => ({
          ...current,
          apiKeySettings,
        }));
      }),
      validationPromise
        .then(({ validation, error }) => {
          if (!isCurrentLoad()) return;
          if (error) {
            setState((current) => ({
              ...current,
              validation: null,
              validationLoading: false,
              error: error instanceof Error ? error.message : String(error),
            }));
            return;
          }
          setState((current) => ({
            ...current,
            validation,
            validationLoading: false,
          }));
        }),
    ]);
  }

  useLayoutEffect(() => {
    missingWorkflowNotifiedRef.current = null;
    void runtimeStatus.refreshRuntime({ silent: true });
    setState(cachedWorkflowRunPageState(workflowId, initialState));
    void loadRequirements();
    setRequiredModelsModalOpen(false);
    clearRunComparisonInputSource();
    clearLivePreview();
    trackedRunsRef.current = [];
    setTrackedRuns([]);
    setFailedTrackedRuns([]);
    setFailedRunSummaryOpen(false);
    setWorkflowCancelConfirmation(null);
    return () => {
      requirementsLoadSequenceRef.current += 1;
    };
  }, [workflowId, runtimeStatus.refreshRuntime]);

  useEffect(() => {
    if (state.firstLoadedWorkflowId !== workflowId || state.packageLoadErrorStatus !== 404) {
      return;
    }
    if (missingWorkflowNotifiedRef.current === workflowId) return;
    missingWorkflowNotifiedRef.current = workflowId;
    invalidateWorkflowRunPageCache(workflowId);
    onMissingWorkflow?.(workflowId);
  }, [onMissingWorkflow, state.firstLoadedWorkflowId, state.packageLoadErrorStatus, workflowId]);

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

  useEffect(() => {
    if (!terminalRuntimeProgress) return undefined;
    if (remainingTrackedRunCount > 0) return undefined;
    const jobId = terminalRuntimeProgress.job_id;
    if (state.result?.job_id === jobId && isReusableRecoveredResult(state.result, shouldWaitForRecoveredOutputPayload)) return undefined;
    if (runtimeResultRecoveryInFlightRef.current === jobId) return undefined;

    runtimeResultRecoveryInFlightRef.current = jobId;
    const recover = async () => {
      for (let attempt = 0; attempt < runtimeResultRecoveryMaxAttempts; attempt += 1) {
        const result = await fetchJobResult(jobId);
        if (activeWorkflowIdRef.current !== workflowId) return;
        if (isEngineJob(result)) {
          if (isTrackableJob(result)) {
            setSubmittedJob(result);
            addTrackedRun(trackedRunFromJob(result));
            return;
          }
          setState((current) => ({
            ...current,
            job: null,
            progress: terminalRuntimeProgress,
            error: null,
          }));
          if (
            result.status === "blocked_by_memory"
            || isBlockingMemoryState(result.memory_status?.state ?? "")
          ) {
            openMemoryFailureDialog(result);
            return;
          }
          if (result.status === "failed") {
            recordTrackedFailure(
              result.queue_id ?? result.job_id,
              result.job_id,
              result.message ?? "Workflow run failed.",
              result.error_code,
              result.message,
              {
                memory_status: result.memory_status ?? null,
                memory_decision: result.memory_decision ?? null,
              },
              result.memory_requirement,
            );
          }
          return;
        }
        if (shouldRetryRecoveredResult(result, shouldWaitForRecoveredOutputPayload)) {
          setState((current) => ({
            ...current,
            progress: terminalRuntimeProgress,
            error: null,
          }));
          await wait(runtimeResultRecoveryRetryMs);
          if (activeWorkflowIdRef.current !== workflowId) return;
          continue;
        }
        handleRecoveredRuntimeResult(result);
        return;
      }
      if (activeWorkflowIdRef.current !== workflowId) return;
      setState((current) => ({
        ...current,
        progress: terminalRuntimeProgress,
        error: null,
      }));
    };
    recover()
      .catch((error) => {
        if (activeWorkflowIdRef.current !== workflowId) return;
        setState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "Could not load the completed workflow result.",
        }));
      })
      .finally(() => {
        if (runtimeResultRecoveryInFlightRef.current === jobId) {
          runtimeResultRecoveryInFlightRef.current = null;
        }
      });
  }, [remainingTrackedRunCount, shouldWaitForRecoveredOutputPayload, state.result, terminalRuntimeProgress?.job_id, terminalRuntimeProgress?.status, workflowId]);

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

  async function refreshWorkflowStatusAfterRun(): Promise<WorkflowStatusResponse | null> {
    // Preparation polling stops the moment the run request resolves, so the
    // last poll can miss the final "ready" install state. Without this final
    // refresh the page would keep a stale intermediate status (preparing,
    // smoke_testing, ...) and re-open the preparation dialog on the next run.
    try {
      const statusResponse = await fetchWorkflowStatus(workflowId);
      setState((current) => ({ ...current, workflowStatus: statusResponse }));
      return statusResponse;
    } catch {
      // Non-fatal: the next run's status polling will self-correct.
      return null;
    }
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

  function clearLivePreview() {
    livePreviewRef.current = null;
    setLivePreview(null);
  }

  async function handleRun() {
    if (!canRun) {
      return;
    }

    const shouldTrackPreparation = shouldShowRunPreparationDialog(workflowStatusForWorkflow);
    let stopPreparationPolling: (() => void) | null = null;
    const stopPreparationTracking = () => {
      if (!stopPreparationPolling) return;
      stopPreparationPolling();
      stopPreparationPolling = null;
    };
    const runCount = clampBatchCount(batchCount);
    const submittedValuesSnapshot = { ...submittedInputValues };
    // Seeds whose value should advance after each queued generation.
    const advanceableSeedIds = Object.keys(seedDefaultModes).filter(
      (id) => seedModes[id] !== "fixed" && typeof submittedValuesSnapshot[id] === "number",
    );
    // Precompute one input set per generation and persist the advanced seed
    // values synchronously, so a rapid next Run press starts from the advanced
    // seeds instead of re-submitting the ones already queued here.
    const generationInputs: Record<string, unknown>[] = [];
    const seedCursor: Record<string, unknown> = { ...submittedValuesSnapshot };
    for (let index = 0; index < runCount; index += 1) {
      generationInputs.push({ ...seedCursor });
      for (const id of advanceableSeedIds) {
        seedCursor[id] = nextSeedValue(seedCursor[id], seedModes[id], inputIndex.get(id)?.validation);
      }
    }
    for (const id of advanceableSeedIds) {
      setInputValue(id, seedCursor[id]);
    }
    const outputPreferencesSnapshot = getOutputPreferencesSnapshot();
    // Pressing Run while this workflow already has an active run queues more
    // runs behind it: keep the active run's progress, live preview, and
    // comparison on screen instead of flashing back to a starting state.
    const queueingBehindActiveRun =
      trackedRunsRef.current.some(isTrackedRunActive) || isActiveWorkflowProgress(displayedProgress);
    beginRunSubmission();
    if (!queueingBehindActiveRun) {
      clearLivePreview();
      resolveRunComparisonInputSource(packageDataForWorkflow, allControls, submittedValuesSnapshot);
    }
    setFailureDialog(null);
    setInputErrorDialog(null);
    if (shouldTrackPreparation) {
      // The cached status may be stale (e.g. a previous run finished after the
      // last poll). Let the polling's immediate first fetch decide from fresh
      // backend state whether the dialog is actually needed, instead of
      // re-opening it from a stale non-ready snapshot.
      stopPreparationPolling = startRunPreparationStatusPolling();
    } else {
      setRunPreparationDialog(null);
    }
    if (queueingBehindActiveRun) {
      setState((current) => ({ ...current, error: null }));
    } else {
      setState((current) => ({
        ...current,
        job: null,
        progress: optimisticProgress(),
        error: null,
      }));
    }

    try {
      for (let index = 0; index < runCount; index += 1) {
        const response = await runWorkflow(workflowId, {
          inputs: { ...generationInputs[index] },
          options: {},
          output_preferences_snapshot: outputPreferencesSnapshot,
        });

        if (!isEngineJob(response)) {
          stopPreparationTracking();
          finishRunSubmission();
          clearRunComparisonInputSource();
          const userError = firstRunUserFixableError(response);
          const message = workflowValidationErrorMessage(response);
          const preparationFailure = response.error_category === "workflow_preparation";
          setState((current) => ({
            ...current,
            validation: userError || preparationFailure ? current.validation : response,
            progress: null,
            error: response.valid || userError || preparationFailure ? null : message,
          }));
          if (!response.valid && userError) {
            setRunPreparationDialog(null);
            void refreshWorkflowStatusAfterRun();
            openInputErrorDialog(userError);
          } else if (!response.valid && preparationFailure) {
            const refreshedStatus = await refreshWorkflowStatusAfterRun();
            setRunPreparationDialog(
              runPreparationDialogFromStatus(refreshedStatus)
                ?? runPreparationDialogFromValidation(response),
            );
          } else if (!response.valid) {
            setRunPreparationDialog(null);
            void refreshWorkflowStatusAfterRun();
            void openFailureDialog(message, null);
          } else {
            setRunPreparationDialog(null);
            void refreshWorkflowStatusAfterRun();
          }
          return;
        }

        setSubmittedJob(response);
        if (isTrackableJob(response)) {
          addTrackedRun(trackedRunFromJob(response));
        } else {
          stopPreparationTracking();
          finishRunSubmission();
          setRunPreparationDialog(null);
          void refreshWorkflowStatusAfterRun();
          return;
        }
      }
      stopPreparationTracking();
      finishRunSubmission();
      setRunPreparationDialog(null);
      void refreshWorkflowStatusAfterRun();
      void pollTrackedRunsDue(true);
    } catch (error) {
      stopPreparationTracking();
      const message = error instanceof Error ? error.message : String(error);
      runtimeStatus.markActionFailure(error);
      void runtimeStatus.refreshRuntime({ force: true, silent: false });
      finishRunSubmission();
      setRunPreparationDialog(null);
      void refreshWorkflowStatusAfterRun();
      clearRunComparisonInputSource();
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
    const fallbackCount = Math.max(trackedCount, activeCancelableRunHandle ? 1 : 0);
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
      : !packageDataForWorkflow
        ? "Workflow context is still loading."
        : undefined;
    return {
      enabled: civitaiConfigured && Boolean(packageDataForWorkflow),
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

  async function openFailureDialog(
    errorMessage: string,
    jobId: string | null,
    errorCode: JobResult["error_code"] = null,
    userMessage: string | null = null,
    developerDetails: Record<string, unknown> = {},
    memoryRequirement: MemoryRequirement | null = null,
  ) {
    const memoryFailure = isMemoryFailureCode(errorCode);
    setFailureDialog({
      errorMessage,
      userMessage,
      errorCode,
      memoryRequirement,
      developerDetails,
      jobId,
      logsLoading: false,
      logsLoaded: false,
      logError: null,
      comfyuiLogs: [],
      noofyLogs: [],
      detailsOpen: false,
      copied: false,
      logsCopied: false,
    });
  }

  async function loadFailureLogsFor(errorMessage: string, jobId: string | null) {
    setFailureDialog((current) =>
      current && current.errorMessage === errorMessage && current.jobId === jobId
        ? { ...current, logsLoading: true, logError: null }
        : current,
    );
    try {
      const response = jobId ? await fetchJobLogs(jobId, { limit: logLimit }) : await fetchLogs({ limit: logLimit });
      const splitLogs = splitDiagnosticLogs(response.events);
      setFailureDialog((current) =>
        current && current.errorMessage === errorMessage && current.jobId === jobId
          ? {
              ...current,
              logsLoading: false,
              logsLoaded: true,
              comfyuiLogs: splitLogs.comfyuiLogs,
              noofyLogs: splitLogs.noofyLogs,
            }
          : current,
      );
      return splitLogs;
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
      throw error;
    }
  }

  async function loadFailureLogs() {
    if (!failureDialog || failureDialog.logsLoading) return;
    await loadFailureLogsFor(failureDialog.errorMessage, failureDialog.jobId);
  }

  async function handleCopyFailureLogs() {
    if (!failureDialog) return;
    await navigator.clipboard.writeText(formatFailureReport(workflowId, failureDialog));
    setFailureDialog((current) => (current ? { ...current, copied: true } : current));
  }

  async function handleCopyFailureDiagnosticLogs() {
    if (!failureDialog) return;
    const dialogSnapshot = failureDialog;
    let comfyuiLogs = dialogSnapshot.comfyuiLogs;
    let noofyLogs = dialogSnapshot.noofyLogs;

    if (!dialogSnapshot.logsLoaded) {
      try {
        const splitLogs = await loadFailureLogsFor(dialogSnapshot.errorMessage, dialogSnapshot.jobId);
        comfyuiLogs = splitLogs.comfyuiLogs;
        noofyLogs = splitLogs.noofyLogs;
      } catch {
        return;
      }
    }

    await navigator.clipboard.writeText(
      formatFailureLogsReport({
        comfyuiLogs,
        noofyLogs,
        logsLoaded: true,
        errorCode: dialogSnapshot.errorCode,
      }),
    );
    setFailureDialog((current) => (current ? { ...current, logsCopied: true } : current));
  }

  function openInputErrorDialog(error: RunUserFixableError) {
    setInputErrorDialog({
      error,
      logsLoading: false,
      logsLoaded: false,
      logError: null,
      comfyuiLogs: [],
      noofyLogs: [],
      detailsOpen: false,
      copied: false,
    });
  }

  async function loadInputErrorLogs() {
    if (!inputErrorDialog || inputErrorDialog.logsLoading) return;
    setInputErrorDialog((current) => current ? { ...current, logsLoading: true, logError: null, detailsOpen: true } : current);
    try {
      const response = await fetchLogs({ limit: logLimit });
      const splitLogs = splitDiagnosticLogs(response.events);
      setInputErrorDialog((current) =>
        current
          ? {
              ...current,
              logsLoading: false,
              logsLoaded: true,
              comfyuiLogs: splitLogs.comfyuiLogs,
              noofyLogs: splitLogs.noofyLogs,
            }
          : current,
      );
    } catch (error) {
      setInputErrorDialog((current) =>
        current
          ? {
              ...current,
              logsLoading: false,
              logError: error instanceof Error ? error.message : String(error),
            }
          : current,
      );
    }
  }

  async function handleCopyInputErrorDetails() {
    if (!inputErrorDialog) return;
    await navigator.clipboard.writeText(formatInputErrorReport(workflowId, inputErrorDialog));
    setInputErrorDialog((current) => (current ? { ...current, copied: true } : current));
  }

  function handleFixInput(error: RunUserFixableError) {
    if (!error.control_id) return;
    focusDashboardControl(error.control_id);
    setInputErrorDialog(null);
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
      const previousPreview = livePreviewRef.current;
      const progress = await fetchJobProgress(handle, {
        sincePreviewSequence: previousPreview?.handle === handle ? previousPreview.sequence : null,
      });
      if (progress.status === "unknown") {
        handleVanishedTrackedRun(run);
        return;
      }
      if (shouldDisplayLivePreviewForHandle(handle)) {
        handleProgressLivePreview(handle, progress);
      }
      const nextRun = trackedRunFromProgress(run, progress, Date.now());
      upsertTrackedRun(nextRun, progress);
      // Background polls of queued runs must not replace the progress of the
      // run that is currently displayed (the bar would flicker back to zero).
      const currentRunAfterUpdate = selectCurrentTrackedRun(trackedRunsRef.current);
      if (!currentRunAfterUpdate || currentRunAfterUpdate.clientId === nextRun.clientId) {
        setState((current) => ({ ...current, progress, error: null }));
      }

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

  function handleProgressLivePreview(handle: string, progress: JobProgress) {
    const preview = progress.live_preview;
    if (!preview?.data_url) return;
    const stored: StoredLivePreview = {
      ...preview,
      target_node_ids: preview.target_node_ids ?? [],
      handle,
    };
    livePreviewRef.current = stored;
    setLivePreview(stored);
  }

  function shouldDisplayLivePreviewForHandle(handle: string) {
    const current = selectCurrentTrackedRun(trackedRunsRef.current);
    return current ? trackedRunHandle(current) === handle : true;
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
      recordTrackedFailure(
        trackedRunHandle(nextRun),
        null,
        progress.message ?? "Workflow run failed.",
        progress.error_code,
        null,
        progress.developer_details,
        progress.memory_requirement,
      );
    }
    pollNextTrackedRunAfterTerminal();
  }

  function handleVanishedTrackedRun(run: TrackedRun) {
    const handle = trackedRunHandle(run);
    const nextRun = trackedRunWithStatus(run, "unknown", null);
    replaceTrackedRuns(
      trackedRunsRef.current.map((tracked) => tracked.clientId === run.clientId ? nextRun : tracked),
    );
    if (livePreviewRef.current?.handle === handle) {
      clearLivePreview();
    }
    workflowTabs?.setWorkflowRuntime(workflowId, {
      activeJobId: null,
      activeJobStatus: "unknown",
      activeJobProgress: null,
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });
    workflowTabs?.setWorkflowRecoveryNotice(workflowId, vanishedRunRecoveryMessage());
    setState((current) => ({
      ...current,
      job: null,
      progress: null,
      error: null,
    }));
    pollNextTrackedRunAfterTerminal();
  }

  function handleTrackedResult(run: TrackedRun, result: JobResult) {
    const nextRun = trackedRunWithStatus(run, result.status, result.error);
    upsertTrackedRun(nextRun);
    if (result.status === "completed" || result.status === "failed") {
      storeRunPageResultSnapshot(workflowId, result);
      setState((current) => cacheRunPageResult(workflowId, current, result));
    }
    if (result.status === "failed") {
      recordTrackedFailure(
        trackedRunHandle(nextRun),
        result.job_id,
        result.error ?? "ComfyUI could not finish this run.",
        result.error_code,
        result.user_message,
        result.developer_details,
        result.memory_requirement,
      );
    }
    if (livePreviewRef.current?.handle === trackedRunHandle(nextRun)) {
      clearLivePreview();
    }
    if (!selectCurrentTrackedRun(trackedRunsRef.current)) {
      recordWorkflowTerminalResult(result);
    }
    pollNextTrackedRunAfterTerminal();
  }

  function handleRecoveredRuntimeResult(result: JobResult) {
    if (!terminalStatuses.has(result.status)) {
      handlePendingRecoveredRuntimeResult(result);
      return;
    }
    storeRunPageResultSnapshot(workflowId, result, {
      progress: null,
      error: result.status === "failed"
        ? cachedWorkflowRunPageState(workflowId, initialState).error
        : null,
    });
    setState((current) => cacheRunPageResult(workflowId, current, result, {
      progress: null,
      error: result.status === "failed" ? current.error : null,
    }));
    if (livePreviewRef.current?.handle === result.job_id) {
      clearLivePreview();
    }
    if (result.status === "failed") {
      recordTrackedFailure(
        result.job_id,
        result.job_id,
        result.error ?? "ComfyUI could not finish this run.",
        result.error_code,
        result.user_message,
        result.developer_details,
        result.memory_requirement,
      );
    }
    recordWorkflowTerminalResult(result);
  }

  function handlePendingRecoveredRuntimeResult(result: JobResult) {
    if (activeWorkflowProgressStatuses.has(result.status)) {
      addTrackedRun(trackedRunFromResult(result));
      setState((current) => ({
        ...current,
        progress: progressFromRecoveredResult(result),
        error: null,
      }));
      return;
    }
    setState((current) => ({
      ...current,
      error: null,
    }));
  }

  function pollNextTrackedRunAfterTerminal() {
    if (selectCurrentTrackedRun(trackedRunsRef.current)) {
      void pollTrackedRunsDue(true);
    }
  }

  function recordTrackedFailure(
    handle: string,
    jobId: string | null,
    message: string,
    errorCode: JobResult["error_code"] = null,
    userMessage: string | null = null,
    developerDetails: Record<string, unknown> = {},
    memoryRequirement: MemoryRequirement | null = null,
  ) {
    setFailedTrackedRuns((current) => {
      if (current.some((item) => item.handle === handle)) return current;
      const next = [...current, { handle, jobId, message, errorCode, userMessage, developerDetails, memoryRequirement }];
      if (next.length === 1) {
        void openFailureDialog(message, jobId ?? handle, errorCode, userMessage, developerDetails, memoryRequirement);
      } else if (next.length > 1) {
        setFailureDialog(null);
      }
      return next;
    });
  }

  function setSubmittedJob(job: EngineJob) {
    const progress = progressFromSubmittedJob(job);
    recordWorkflowJob(job, progress);
    // A submission that queues behind a different active run must not replace
    // the displayed progress of the run that is currently executing.
    const currentRun = selectCurrentTrackedRun(trackedRunsRef.current);
    const keepDisplayedProgress = Boolean(currentRun && !progressMatchesTrackedRun(progress, currentRun));
    setState((current) => ({
      ...current,
      job,
      progress: keepDisplayedProgress ? current.progress : progress,
    }));
    if (job.status === "blocked_by_memory" || isBlockingMemoryState(job.memory_status?.state ?? "")) {
      openMemoryFailureDialog(job);
    }
  }

  function openMemoryFailureDialog(job: EngineJob) {
    void openFailureDialog(
      "Not enough memory to run this workflow",
      job.job_id,
      "insufficient_memory",
      MEMORY_FAILURE_MESSAGE,
      {
        job_id: job.job_id,
        workflow_id: workflowId,
        memory_status: job.memory_status ?? null,
        memory_decision: job.memory_decision ?? null,
        memory_requirement: job.memory_requirement ?? null,
      },
      job.memory_requirement ?? null,
    );
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
      activeJobProgress: terminalProgressFromResult(result),
      activeJobUpdatedAt: Date.now(),
      handleSource: null,
      queueId: null,
    });
  }

  const unresolvedModelSummary = activeModelSummary?.models.filter((model) => model.status !== "available") ?? [];
  const missingModels = unresolvedModelSummary.length > 0 ? unresolvedModelSummary : activeValidation?.missing_models ?? [];
  const workflowSummary = workflowStatusForWorkflow?.workflow;
  const workflowNameSource = workflowSummary ?? packageDataForWorkflow?.metadata ?? packageDataForWorkflow;
  const workflowDisplayTitle = workflowDisplayName(workflowNameSource);
  const dashboardSetupRequired = Boolean(
    packageDataForWorkflow && packageNeedsDashboardSetup(packageDataForWorkflow, workflowSummary),
  );

  useEffect(() => {
    if (!workflowNameSource) return;
    const name = workflowDisplayName(workflowNameSource);
    if (name) onWorkflowNameChange?.(name);
  }, [
    packageDataForWorkflow?.display_name,
    packageDataForWorkflow?.metadata?.display_name,
    packageDataForWorkflow?.metadata?.name,
    workflowSummary?.display_name,
    workflowSummary?.name,
    workflowNameSource,
  ]);

  useLayoutEffect(() => {
    if (!dashboardSetupRequired || !packageDataForWorkflow || !onConfigureDashboard) return;
    const redirectKey = [
      workflowId,
      packageDataForWorkflow.dashboard.status,
      dashboardUserStateVersion(packageDataForWorkflow),
    ].join(":");
    if (dashboardSetupRouteRequestedRef.current === redirectKey) return;
    dashboardSetupRouteRequestedRef.current = redirectKey;
    onConfigureDashboard(workflowId, workflowDisplayTitle);
  }, [dashboardSetupRequired, onConfigureDashboard, packageDataForWorkflow, workflowDisplayTitle, workflowId]);

  useWorkflowRunnerLease({
    workflowId,
    workflowTabs,
    packageReady: Boolean(packageDataForWorkflow),
    dashboardSetupRequired,
    runner: workflowStatusForWorkflow?.runner ?? null,
  });

  const installStatus = typeof workflowStatusForWorkflow?.install?.status === "string"
    ? workflowStatusForWorkflow.install.status
    : null;
  const memoryStatus = activeMemoryStatus;
  const memoryNotice = memoryStatus ? memoryStatusDisplay(memoryStatus) : null;
  const memoryDiagnostics = memoryStatus
    ? memoryStatusDeveloperDetails(state.job, displayedProgress)
    : null;
  const showMemoryLoadedPill = Boolean(memoryStatus && isWarmReusableMemoryState(memoryStatus.state));
  const showUserFacingMemoryNotice = Boolean(
    memoryNotice
      && !showMemoryLoadedPill
      && memoryNotice.title !== "Checking memory"
      && !(memoryStatus && isSilentQueuedMemoryState(memoryStatus.state))
      && !(memoryStatus && isBlockingMemoryState(memoryStatus.state)),
  );
  const backendKnownUnreachable = runtimeStatus.backendStatus === "unreachable";
  const engineKnownUnavailable =
    !isRunning &&
    runtimeStatus.backendStatus === "reachable" &&
    runtimeStatus.engineStatus === "offline";
  const memoryRefusesRun = Boolean(memoryStatus && isBlockingMemoryState(memoryStatus.state));
  const dashboardPackagePending = state.firstLoadedWorkflowId !== workflowId;
  const dashboardValuesReady = !dashboardPackagePending && userStateLoaded;
  const dashboardLoadingCopy = dashboardPackagePending
    ? {
        title: "Loading workflow",
        message: "Loading the controls for this workflow.",
      }
    : {
        title: "Loading saved inputs",
        message: "Restoring your saved input values.",
      };
  const runReadinessPending = modelSummaryLoadingForWorkflow || validationLoadingForWorkflow;
  // An active run does not disable Run: pressing it again queues another run
  // behind the current one. Only real blockers gate the button.
  const canRun = Boolean(
    dashboardValuesReady
      && !runReadinessPending
      && workflowStatusForWorkflow?.can_prepare !== false
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
        isWaitingForMemory,
        dashboardLoadingReason: !dashboardValuesReady ? `${dashboardLoadingCopy.title}...` : null,
        memoryStatus,
        missingModels,
        modelSummaryLoading: modelSummaryLoadingForWorkflow,
        modelSummaryReady: activeModelSummary?.ready_to_run,
        validation: activeValidation,
        validationLoading: validationLoadingForWorkflow,
        workflowStatus: workflowStatusForWorkflow,
      });
  const canCancel = Boolean(
    (remainingTrackedRunCount > 0 || (isRunning && (state.job || activeCancelableRunHandle))) && !isBlockedByMemory,
  );
  const progressPercent =
    displayedProgress?.value !== null && displayedProgress?.value !== undefined && displayedProgress.max
      ? Math.min(100, Math.round((displayedProgress.value / displayedProgress.max) * 100))
      : displayedProgress?.status === "completed"
        ? 100
        : 0;
  const cancelTooltip = remainingTrackedRunCount > 1
    ? "Cancel current run and all queued runs for this workflow"
    : "Cancel current run";
  const previewProgressMessage = progressMessage(displayedProgress, state.result, memoryStatus);
  const topBarProgress = isRunning ? {
    percent: progressPercent,
    remainingCount: remainingTrackedRunCount || undefined,
    onCancelRemaining: remainingTrackedRunCount > 0 ? () => void handleCancel() : undefined,
    cancelRemainingTitle: "Cancel current run and all queued runs for this workflow",
  } : null;

  const inputControls = allControls.filter(
    (c) => c.type === "note" || c.type === "api_credential" || (c.type !== "result_image" && c.type !== "display_image" && c.type !== "display_audio" && c.type !== "display_text" && c.type !== "display_video" && c.type !== "display_file" && c.type !== "display_3d" && c.input_id),
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
    packageDataForWorkflow?.dashboard?.status === "configured" && allControls.length > 0,
  );
  const showCanvasView = viewMode === "canvas" && (dashboardPackagePending || hasDashboard);
  const isEditingLayout = draftLayoutOverrides !== null;
  const creatorActionBarPosition = actionBarPositionFromDashboard(
    packageDataForWorkflow?.dashboard?.presentation?.action_bar,
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
      if (draftActionBarTouched && draftActionBarPosition && packageDataForWorkflow) {
        await saveDashboard(
          workflowId,
          dashboardSavePayloadWithActionBarPosition(packageDataForWorkflow, draftActionBarPosition),
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

  function handleCanvasControlTitleChange(controlId: string, title: string) {
    setState((current) => updatePackageDashboardTitle(current, "control", controlId, title));
  }

  function handleCanvasGroupTitleChange(groupId: string, title: string) {
    setState((current) => updatePackageDashboardTitle(current, "group", groupId, title));
  }

  async function handleCanvasControlTitleCommit(controlId: string, title: string) {
    if (!packageDataForWorkflow) return;
    try {
      await saveDashboard(
        workflowId,
        dashboardSavePayloadWithTitle(packageDataForWorkflow, "control", controlId, title),
      );
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  async function handleCanvasGroupTitleCommit(groupId: string, title: string) {
    if (!packageDataForWorkflow) return;
    try {
      await saveDashboard(
        workflowId,
        dashboardSavePayloadWithTitle(packageDataForWorkflow, "group", groupId, title),
      );
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

  const workflowActionBarRunState: WorkflowActionBarRunState = {
    isRunning,
    canRun,
    canCancel,
    memoryLoaded: showMemoryLoadedPill,
    cancelTitle: cancelTooltip,
    showStatusNotice: showUserFacingMemoryNotice,
    statusTitle: showUserFacingMemoryNotice ? memoryNotice?.title ?? null : null,
    statusMessage: showUserFacingMemoryNotice ? memoryNotice?.message ?? null : null,
    disabledReason: memoryRefusesRun ? null : runDisabledReason,
    disabledActionLabel: hasRequiredModelFixAction ? "Download" : null,
    developerDetails: showUserFacingMemoryNotice ? memoryDiagnostics : null,
  };

  const classicWorkflowActions = (
    <WorkflowActionBar
      className="workflow-action-bar--inline workflow-action-bar--preview-compact"
      runState={{
        ...workflowActionBarRunState,
        memoryLoaded: false,
        showStatusNotice: false,
        disabledActionLabel: null,
        developerDetails: null,
      }}
      batchCount={batchCount}
      switchViewLabel="Switch to Canvas view"
      onRun={() => void handleRun()}
      onBatchCountChange={setBatchCount}
      onCancel={() => void handleCancel()}
      onSwitchView={() => setViewMode("canvas")}
      onExportNoofy={() => setExportDialog({ extension: ".noofy", url: exportWorkflowUrl(workflowId) })}
      onExportComfyJson={() => setExportDialog({ extension: ".json", url: exportWorkflowComfyJsonUrl(workflowId) })}
      onDisabledRunAction={hasRequiredModelFixAction ? () => setRequiredModelsModalOpen(true) : undefined}
      onRestoreDefaults={() => void handleRestoreDefaults()}
      onEnterEditLayout={() => {
        handleEnterEditLayout();
        setViewMode("canvas");
      }}
      onSaveLayout={() => void handleSaveLayout()}
      onCancelLayoutEdit={handleCancelLayoutEdit}
      onEditWidgets={onEditWidgets ? handleEditWidgets : undefined}
    />
  );

  const recoveryNoticeElement = workflowTabs?.recoveryNoticeByWorkflowId[workflowId] ? (
    <div className="notice notice--compact" role="status">
      <RotateCcw size={16} aria-hidden="true" />
      <div>
        <strong>Run cleared</strong>
        <span>{workflowTabs.recoveryNoticeByWorkflowId[workflowId]}</span>
      </div>
      <button
        className="secondary-button secondary-button--small"
        type="button"
        onClick={() => workflowTabs.dismissWorkflowRecoveryNotice(workflowId)}
      >
        Dismiss
      </button>
    </div>
  ) : null;
  const workflowMissing = state.firstLoadedWorkflowId === workflowId && state.packageLoadErrorStatus === 404;
  const workflowRefreshRequired =
    runtimeStatus.pageRefreshRequired || Boolean(state.packageLoadError && !workflowMissing);
  const workflowRefreshMessage = runtimeStatus.pageRefreshRequired
    ? "Noofy restarted in the background. Reload this workflow to reconnect it to the current session."
    : "Noofy could not load this workflow. Reload it before continuing.";

  const notices = (
    <>
      {recoveryNoticeElement}
      {!workflowRefreshRequired && state.error ? (
        <div className="notice notice--error" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>The workflow is not ready</strong>
            <span>{state.error ?? "Restart Noofy, then try again."}</span>
          </div>
        </div>
      ) : null}
      {runtimeStatus.backendStatus === "unreachable" && !isRunning ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>Noofy is offline</strong>
            <span>{runtimeStatus.refreshError ?? "Restart Noofy before running this workflow."}</span>
          </div>
        </div>
      ) : null}
      {engineKnownUnavailable ? (
        <div className="notice notice--warning" role="status">
          <AlertCircle size={18} aria-hidden="true" />
          <div>
            <strong>{runtimeStatus.engineStatus === "starting" ? "Starting ComfyUI" : "ComfyUI is not responding"}</strong>
            <span>Open Engine Settings to finish setup or restart ComfyUI before running this workflow.</span>
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
      workflowName={workflowDisplayTitle}
      onClose={() => setFailureDialog(null)}
      onToggleDetails={() => setFailureDialog((current) => current ? { ...current, detailsOpen: !current.detailsOpen } : current)}
      onViewLogs={() => void loadFailureLogs()}
      onCopy={() => void handleCopyFailureLogs()}
      onCopyLogs={() => void handleCopyFailureDiagnosticLogs()}
    />
  ) : null;
  const inputErrorDialogElement = inputErrorDialog ? (
    <WorkflowInputErrorDialog
      dialog={inputErrorDialog}
      workflowId={workflowId}
      workflowName={workflowDisplayTitle}
      onClose={() => setInputErrorDialog(null)}
      onFixInput={() => handleFixInput(inputErrorDialog.error)}
      onToggleDetails={() => setInputErrorDialog((current) => current ? { ...current, detailsOpen: !current.detailsOpen } : current)}
      onViewLogs={() => void loadInputErrorLogs()}
      onCopy={() => void handleCopyInputErrorDetails()}
    />
  ) : null;
  const failedRunSummaryElement = failedTrackedRuns.length > 1 ? (
    <BatchFailureSummary
      failedRuns={failedTrackedRuns}
      expanded={failedRunSummaryOpen}
      onToggle={() => setFailedRunSummaryOpen((open) => !open)}
      onOpenLogs={(run) => void openFailureDialog(
        run.message,
        run.jobId ?? run.handle,
        run.errorCode,
        run.userMessage ?? null,
        run.developerDetails ?? {},
        run.memoryRequirement ?? null,
      )}
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
    <RunPreparationDialog
      dialog={runPreparationDialog}
      workflowId={workflowId}
      workflowName={workflowDisplayTitle}
      onClose={() => setRunPreparationDialog(null)}
    />
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
    description: packageDataForWorkflow?.metadata?.description ?? workflowSummary?.description ?? "",
    author: packageDataForWorkflow?.metadata?.author ?? "",
    website: packageDataForWorkflow?.metadata?.website ?? "",
    category: packageDataForWorkflow?.metadata?.category ?? workflowSummary?.category ?? "",
    tags: packageDataForWorkflow?.metadata?.tags ?? workflowSummary?.tags ?? [],
    icon: packageDataForWorkflow?.metadata?.icon ?? workflowSummary?.icon ?? "",
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
      inputValues={submittedInputValues}
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
  const workflowRefreshDialogElement = workflowRefreshRequired ? (
    <WorkflowRefreshRequiredDialog
      message={workflowRefreshMessage}
      onRefresh={runtimeStatus.refreshPage}
    />
  ) : null;

  if (workflowMissing) {
    return (
      <AppLayout activeRoute={null} onNavigate={onNavigate}>
        <WorkflowMissingPanel onBack={onBack} />
      </AppLayout>
    );
  }

  if (dashboardSetupRequired) {
    return (
      <AppLayout activeRoute={null} onNavigate={onNavigate}>
        <DashboardSetupRequired
          workflowName={workflowDisplayTitle}
          onBack={onBack}
          onContinue={
            onConfigureDashboard
              ? () => onConfigureDashboard(workflowId, workflowDisplayTitle)
              : undefined
          }
        />
        {workflowRefreshDialogElement}
      </AppLayout>
    );
  }

  if (showCanvasView) {
    return (
      <AppLayout
        activeRoute={null}
        onNavigate={onNavigate}
        mainClassName="main-workspace--canvas-run"
        contentClassName="workspace-content--canvas-run"
        progress={topBarProgress}
      >
        <WorkflowDefaultAssetProvider workflowId={workflowId}>
          <CanvasDashboardView
            controls={allControls}
            groups={allGroups}
            inputIndex={inputIndex}
            outputIndex={outputIndex}
            outputImagesByNodeId={outputImagesByNodeId}
            outputAudiosByNodeId={outputAudiosByNodeId}
            outputTextsByNodeId={outputTextsByNodeId}
            outputVideosByNodeId={outputVideosByNodeId}
            outputFilesByNodeId={outputFilesByNodeId}
            outputThreeDsByNodeId={outputThreeDsByNodeId}
            livePreview={activeLivePreview}
            comparisonBeforeImageUrl={comparisonInputImageUrl}
            valuesReady={dashboardValuesReady}
            loadingTitle={dashboardLoadingCopy.title}
            loadingMessage={dashboardLoadingCopy.message}
            inputValues={inputValues}
            seedModes={seedModes}
            onSeedModeChange={handleSeedModeChange}
            outputPreferences={outputPreferences}
            gallerySaveByControlId={gallerySaveByControlId}
            layoutOverrides={draftLayoutOverrides ?? layoutOverrides}
            actionBarPosition={canvasActionBarPosition}
            isEditingLayout={isEditingLayout}
            runState={workflowActionBarRunState}
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
            onSwitchView={() => setViewMode("classic")}
            onDisabledRunAction={hasRequiredModelFixAction ? () => setRequiredModelsModalOpen(true) : undefined}
            onRestoreDefaults={() => void handleRestoreDefaults()}
            onEnterEditLayout={handleEnterEditLayout}
            onSaveLayout={() => void handleSaveLayout()}
            onCancelLayoutEdit={handleCancelLayoutEdit}
            onEditWidgets={onEditWidgets ? handleEditWidgets : undefined}
            onControlTitleChange={handleCanvasControlTitleChange}
            onControlTitleCommit={(controlId, title) => void handleCanvasControlTitleCommit(controlId, title)}
            onGroupTitleChange={handleCanvasGroupTitleChange}
            onGroupTitleCommit={(groupId, title) => void handleCanvasGroupTitleCommit(groupId, title)}
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
        </WorkflowDefaultAssetProvider>
        {recoveryNoticeElement || failedRunSummaryElement ? (
          <div className="canvas-run-floating-notices">
            {recoveryNoticeElement}
            {failedRunSummaryElement}
          </div>
        ) : null}
        {workflowRefreshDialogElement}
        {workflowCancelConfirmationElement}
        {inputErrorDialogElement}
        {failureDialogElement}
        {preparationDialogElement}
        {loraBrowserElement}
        {exportDialogElement}
        {requiredModelsModalElement}
      </AppLayout>
    );
  }

  return (
    <AppLayout
      activeRoute={null}
      onNavigate={onNavigate}
      mainClassName="main-workspace--workflow-run-classic"
      contentClassName="workspace-content--workflow-run-classic"
      progress={topBarProgress}
    >
      {notices}
      {failedRunSummaryElement}

      <section className="run-workspace run-workspace--classic">
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

          {!dashboardValuesReady ? (
            <div className="workflow-values-loading" role="status" aria-live="polite">
              <Loader2 className="spin" size={20} aria-hidden="true" />
              <div>
                <strong>{dashboardLoadingCopy.title}</strong>
                <span>{dashboardLoadingCopy.message}</span>
              </div>
            </div>
          ) : hasDashboard ? (
            <WorkflowDefaultAssetProvider workflowId={workflowId}>
              <DashboardInputControls
                items={inputTopLevelItems}
                inputIndex={inputIndex}
                inputValues={inputValues}
                seedModes={seedModes}
                onSeedModeChange={handleSeedModeChange}
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
            </WorkflowDefaultAssetProvider>
          ) : (
            <FallbackInputs
              inputValues={inputValues}
              inputs={packageDataForWorkflow?.inputs ?? []}
              onChange={(id, value) => setInputValue(id, value)}
            />
          )}

        </form>

        <aside className="preview-panel preview-panel--pinned">
          <div className="panel-heading">
            <div>
              <h2>Preview</h2>
              {previewProgressMessage ? <p>{previewProgressMessage}</p> : null}
            </div>
            <div className="preview-panel__actions">
              {classicWorkflowActions}
            </div>
          </div>

          <div className={`preview-stage${activeLivePreview ? " preview-stage--live" : ""}`}>
            {classicVisualImageUrl ? (
              comparisonInputImageUrl ? (
                <ImageComparisonSlider
                  beforeSrc={comparisonInputImageUrl}
                  afterSrc={classicVisualImageUrl}
                  alt={activeLivePreview ? "Live generation preview" : "Generated workflow output"}
                  comparisonEnabled={!activeLivePreview}
                />
              ) : (
                <RetainedImage
                  src={classicVisualImageUrl}
                  alt={activeLivePreview ? "Live generation preview" : "Generated workflow output"}
                />
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
              <ThreeDViewer url={classicPreviewMedia.media.url} filename={classicPreviewMedia.media.filename} size={classicPreviewMedia.media.size} autoPreviewUnknownSize />
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
                <strong>Run stopped</strong>
                <span>{state.result.user_message ?? "The run stopped before it finished."}</span>
              </div>
            </div>
          ) : null}
        </aside>
      </section>
      {failureDialogElement}
      {inputErrorDialogElement}
      {workflowCancelConfirmationElement}
      {preparationDialogElement}
      {loraBrowserElement}
      {exportDialogElement}
      {requiredModelsModalElement}
      {workflowRefreshDialogElement}
    </AppLayout>
  );
}
