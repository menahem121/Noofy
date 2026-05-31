import { getJson, postJson } from "./client";

export type JobStatus =
  | "queued"
  | "queued_pending_memory"
  | "blocked_by_memory"
  | "running"
  | "completed"
  | "failed"
  | "canceled"
  | "missing_models"
  | "unknown";

export interface MemoryStatus {
  state: string;
  message: string;
  risk_level: "low" | "medium" | "high" | "unknown" | string;
  queue_id: string | null;
  can_cancel: boolean;
  can_retry_after_cleanup: boolean;
}

export interface EngineJob {
  job_id: string;
  workflow_id: string;
  engine: string;
  status: JobStatus;
  queue_id?: string | null;
  message?: string | null;
  memory_decision?: Record<string, unknown> | null;
  memory_status?: MemoryStatus | null;
}

export interface JobProgress {
  job_id: string;
  status: JobStatus;
  value: number | null;
  max: number | null;
  current_node: string | null;
  message: string | null;
}

export interface JobResult {
  job_id: string;
  status: JobStatus;
  outputs: Array<Record<string, unknown>>;
  error: string | null;
}

export interface DiagnosticEvent {
  id: number;
  timestamp: string;
  level: string;
  message: string;
  source: string;
  job_id: string | null;
  workflow_id: string | null;
  details: Record<string, unknown>;
}

export interface DiagnosticLogResponse {
  events: DiagnosticEvent[];
}

export type GallerySaveState = "queued" | "saving" | "saved" | "saved_with_errors" | "failed" | "canceled" | "interrupted" | "unavailable";

export interface GallerySaveRequest {
  job_id: string;
  control_id: string;
  status: GallerySaveState;
  message: string | null;
  bytes_copied: number;
  total_bytes: number | null;
  item_ids: string[];
  updated_at: string;
}

export interface GalleryJobSaveStatus {
  job_id: string;
  outputs: GallerySaveRequest[];
}

export function isEngineJob(response: unknown): response is EngineJob {
  return Boolean(response && typeof response === "object" && "job_id" in response && "engine" in response);
}

export function fetchJobProgress(jobId: string) {
  return getJson<JobProgress>(`/jobs/${jobId}/progress`);
}

export function fetchJobResult(jobId: string) {
  return getJson<JobResult | EngineJob>(`/jobs/${jobId}/result`);
}

export function fetchJobLogs(jobId: string, options: { limit?: number } = {}) {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return getJson<DiagnosticLogResponse>(`/jobs/${encodeURIComponent(jobId)}/logs${suffix}`);
}

export function fetchLogs(options: { limit?: number } = {}) {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return getJson<DiagnosticLogResponse>(`/logs${suffix}`);
}

export function cancelJob(jobId: string) {
  return postJson<JobProgress>(`/jobs/${jobId}/cancel`);
}

export function fetchJobGalleryStatus(jobId: string) {
  return getJson<GalleryJobSaveStatus>(`/jobs/${encodeURIComponent(jobId)}/gallery`);
}

export function saveJobOutputToGallery(jobId: string, controlId: string) {
  return postJson<GallerySaveRequest>(`/jobs/${encodeURIComponent(jobId)}/gallery/${encodeURIComponent(controlId)}`);
}

export function cancelJobOutputGallerySave(jobId: string, controlId: string) {
  return postJson<GallerySaveRequest>(`/jobs/${encodeURIComponent(jobId)}/gallery/${encodeURIComponent(controlId)}/cancel`);
}
