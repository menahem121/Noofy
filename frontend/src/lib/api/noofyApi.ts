export interface WorkflowSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  publisher_id?: string;
  package_id?: string;
  trust_level?: string;
  status?: string;
  status_label?: string;
  unresolved_input_count?: number;
  custom_node_count?: number;
  required_model_count?: number;
}

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

export interface RuntimeDependencyStatus {
  name: string;
  available: boolean;
  error: string | null;
}

export interface RuntimeEnvironmentStatus {
  prepared: boolean;
  dependencies?: RuntimeDependencyStatus[];
  error?: string | null;
}

export interface RuntimeStatus {
  mode: "external" | "managed";
  reachable: boolean;
  base_url: string;
  repo_dir: string;
  managed_process_running: boolean;
  pid: number | null;
  error: string | null;
  environment: RuntimeEnvironmentStatus | null;
  crash_count: number;
  restart_attempt: number;
  max_restart_attempts: number;
  uptime_seconds: number | null;
  last_crash_at: string | null;
}

export interface WorkflowHealthSummary {
  workflow_id: string;
  valid: boolean;
  missing_model_count: number;
  error_count: number;
}

export interface BackendHealthReport {
  status: string;
  comfyui: RuntimeStatus;
  workflow_package_count: number;
  workflows: WorkflowHealthSummary[];
  latest_error: unknown | null;
}

export interface MissingModel {
  folder: string;
  filename: string;
  source_url: string | null;
  checksum: string | null;
}

export interface WorkflowValidationResult {
  workflow_id: string;
  valid: boolean;
  missing_models: MissingModel[];
  errors: string[];
}

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

export interface WorkflowRunPayload {
  inputs: Record<string, unknown>;
  options?: Record<string, unknown>;
}

export type WorkflowRunResponse = EngineJob | WorkflowValidationResult;

export interface WorkflowImportResponse {
  workflow_id: string;
  status: "imported" | "needs_input_setup" | "cannot_prepare_automatically" | string;
  user_facing_message: string;
  workflow: WorkflowSummary;
  required_model_count: number;
  custom_node_count: number;
  unresolved_input_count: number;
}

declare global {
  interface Window {
    __NOOFY_RUNTIME_CONFIG__?: {
      apiBaseUrl?: string;
      apiToken?: string;
    };
  }
}

const DEFAULT_API_BASE_URL = "/api";
const configuredApiBaseUrl = import.meta.env.VITE_NOOFY_API_BASE_URL as string | undefined;
const configuredApiToken = import.meta.env.VITE_NOOFY_API_TOKEN as string | undefined;

export function getApiBaseUrl() {
  const runtimeBaseUrl = window.__NOOFY_RUNTIME_CONFIG__?.apiBaseUrl;
  const baseUrl = runtimeBaseUrl || configuredApiBaseUrl || DEFAULT_API_BASE_URL;
  return baseUrl.replace(/\/$/, "");
}

export function getApiToken() {
  return window.__NOOFY_RUNTIME_CONFIG__?.apiToken || configuredApiToken || null;
}

function apiHeaders(contentType?: string) {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  const token = getApiToken();

  if (contentType) {
    headers["Content-Type"] = contentType;
  }

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  return headers;
}

export function createJobEventsUrl(jobId: string) {
  const url = `${getApiBaseUrl()}/jobs/${encodeURIComponent(jobId)}/events`;
  const token = getApiToken();
  if (!token) {
    return url;
  }
  return `${url}?token=${encodeURIComponent(token)}`;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    headers: apiHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/json"),
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function postBytes<T>(path: string, body: ArrayBuffer): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    headers: apiHeaders("application/octet-stream"),
    body,
  });

  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function fetchRuntimeStatus() {
  return getJson<RuntimeStatus>("/runtime");
}

export function fetchHealth() {
  return getJson<BackendHealthReport>("/health");
}

export function fetchWorkflows() {
  return getJson<WorkflowSummary[]>("/workflows");
}

export async function importWorkflowPackage(file: File) {
  const data = await readFileAsArrayBuffer(file);
  return postBytes<WorkflowImportResponse>(
    `/workflows/import?filename=${encodeURIComponent(file.name)}`,
    data,
  );
}

function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") {
    return file.arrayBuffer();
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) {
        resolve(reader.result);
        return;
      }
      reject(new Error("Workflow file could not be read."));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Workflow file could not be read."));
    reader.readAsArrayBuffer(file);
  });
}

export function validateWorkflow(workflowId: string) {
  return postJson<WorkflowValidationResult>(`/workflows/${workflowId}/validate`);
}

export function runWorkflow(workflowId: string, payload: WorkflowRunPayload) {
  return postJson<WorkflowRunResponse>(`/workflows/${workflowId}/run`, payload);
}

export function fetchJobProgress(jobId: string) {
  return getJson<JobProgress>(`/jobs/${jobId}/progress`);
}

export function fetchJobResult(jobId: string) {
  return getJson<JobResult | EngineJob>(`/jobs/${jobId}/result`);
}

export function cancelJob(jobId: string) {
  return postJson<JobProgress>(`/jobs/${jobId}/cancel`);
}

export function bootstrapEngine() {
  return postJson<unknown>("/engine/comfyui/bootstrap");
}

export function startEngine() {
  return postJson<unknown>("/engine/comfyui/start");
}

export function stopEngine() {
  return postJson<unknown>("/engine/comfyui/stop");
}

export function isEngineJob(response: unknown): response is EngineJob {
  return Boolean(response && typeof response === "object" && "job_id" in response && "engine" in response);
}

// ─── Gallery ────────────────────────────────────────────────────────────────

/**
 * User-facing settings used when an image was generated.
 * Keys are beginner labels (e.g. "Prompt", "Style", "Aspect ratio").
 * Raw ComfyUI node data must never appear here.
 */
export type GalleryImageSettings = Record<string, string | number | boolean>;

export interface GalleryImage {
  id: string;
  /** URL or relative path for the grid thumbnail (may be same as imageUrl) */
  thumbnailUrl: string;
  /** URL or relative path for the full-resolution image */
  imageUrl: string;
  workflowId: string;
  workflowName: string;
  /** The text prompt the user typed, if applicable */
  prompt: string;
  /** ISO-8601 timestamp */
  createdAt: string;
  width: number;
  height: number;
  favorite: boolean;
  /**
   * User-facing workflow widget values at the time of generation.
   * Only values the user could edit in the Noofy workflow UI.
   */
  usedSettings: GalleryImageSettings;
  /** Backend file reference (path or output ref) — not shown in default UI */
  fileRef: string;
}

export interface GalleryResponse {
  images: GalleryImage[];
  total: number;
}

export function fetchGallery(): Promise<GalleryResponse> {
  return getJson<GalleryResponse>("/gallery");
}
