import type {
  DashboardControlDef,
  DiagnosticEvent,
  GallerySaveRequest,
  JobLivePreview,
  JobProgress,
  JobResult,
  MemoryRequirement,
  RunUserFixableError,
  WorkflowInputDef,
} from "../../lib/api/noofyApi";

export interface RunFailureDialogState {
  errorMessage: string;
  userMessage: string | null;
  errorCode: JobResult["error_code"];
  memoryRequirement: MemoryRequirement | null;
  developerDetails: Record<string, unknown>;
  jobId: string | null;
  logsLoading: boolean;
  logsLoaded: boolean;
  logError: string | null;
  comfyuiLogs: DiagnosticEvent[];
  noofyLogs: DiagnosticEvent[];
  detailsOpen: boolean;
  copied: boolean;
  logsCopied: boolean;
}

export interface RunInputErrorDialogState {
  error: RunUserFixableError;
  logsLoading: boolean;
  logsLoaded: boolean;
  logError: string | null;
  comfyuiLogs: DiagnosticEvent[];
  noofyLogs: DiagnosticEvent[];
  detailsOpen: boolean;
  copied: boolean;
}

export interface FailedTrackedRun {
  handle: string;
  jobId: string | null;
  message: string;
  userMessage?: string | null;
  errorCode?: JobResult["error_code"];
  memoryRequirement?: MemoryRequirement | null;
  developerDetails?: Record<string, unknown>;
}

export type StoredLivePreview = JobLivePreview & { handle: string };

export interface WorkflowCancelConfirmationState {
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

export type TrackedRun =
  | (TrackedRunBase & { type: "queue"; queueId: string; jobId?: string | null })
  | (TrackedRunBase & { type: "job"; jobId: string; queueId?: string | null });

export type PreparationPhaseStatus = "pending" | "active" | "passed" | "failed" | "blocked";

export interface PreparationPhase {
  id: string;
  label: string;
  status: PreparationPhaseStatus;
}

export interface RunPreparationDialogState {
  message: string;
  detail: string | null;
  phases: PreparationPhase[];
  failed: boolean;
  developerDetailsAvailable: boolean;
}

export interface LoraBrowserDialogState {
  control: DashboardControlDef;
  input: WorkflowInputDef;
}

export type ComparisonImageSource =
  | { kind: "uploaded_asset"; workflowId: string; inputId: string; assetId: string }
  | { kind: "masked_source_asset"; workflowId: string; inputId: string; maskedAssetId: string; sourceAssetId: string }
  | { kind: "package_asset"; workflowId: string; inputId: string; assetId: string }
  | { kind: "gallery_reference"; workflowId: string; inputId: string; galleryItemId: string };

export type GallerySaveByControlId = Record<string, GallerySaveRequest>;
