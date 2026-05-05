export interface WorkflowTrustSummary {
  level: string;
  label: string;
  summary: string;
  badge_tone: "verified" | "locked" | "community" | "unsupported" | string;
  can_prepare_automatically: boolean;
  requires_explicit_opt_in: boolean;
  source_policy: string;
  signature_status: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  publisher_id?: string;
  package_id?: string;
  trust_level?: string;
  trust?: WorkflowTrustSummary;
  status?: string;
  status_label?: string;
  unresolved_input_count?: number;
  custom_node_count?: number;
  required_model_count?: number;
}

export interface WorkflowStatusResponse {
  workflow_id: string;
  workflow: WorkflowSummary;
  install: Record<string, unknown>;
  required_actions: unknown[];
  compatibility_guidance: unknown[];
  runner: Record<string, unknown> | null;
  runner_status: string;
  can_prepare: boolean;
  can_cancel_preparation: boolean;
  can_cancel_job: boolean;
}

export interface TrustPolicyResponse {
  schema_version: string;
  signature_payload_schema_version: string;
  development_hmac_allowed?: boolean;
  trusted_key_count: number;
  trusted_keys: Array<{
    key_id: string;
    algorithm: string;
    purpose: string;
    revoked?: boolean;
    not_before?: string | null;
    expires_at?: string | null;
    policy_versions?: string[];
  }>;
  trust_levels: Record<string, {
    label: string;
    summary: string;
    source_policy: string;
    requires_explicit_opt_in: boolean;
    can_prepare_automatically: boolean;
  }>;
  imported_trusted_claims_require_verified_evidence: boolean;
  secrets_exposed: boolean;
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

export interface ComfyUIRuntimeVersion {
  active_tag: string | null;
  source_hash: string | null;
  source_kind: string;
  local_validation_status: string | null;
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
  version?: ComfyUIRuntimeVersion | null;
  managed_vram_mode?: ComfyUIVramMode | string;
}

export type ComfyUIVramMode = "normal" | "gpu_only" | "highvram" | "lowvram" | "novram" | "cpu";

export interface ComfyUILaunchOption {
  value: ComfyUIVramMode;
  label: string;
  description: string;
}

export interface ComfyUILaunchSettings {
  vram_mode: ComfyUIVramMode;
  options: ComfyUILaunchOption[];
  applies_to_managed_runtime: boolean;
  disabled_reason: string | null;
}

export interface ComfyUILaunchSettingsUpdateResult {
  status: string;
  settings: ComfyUILaunchSettings;
  restart_status: string | null;
  error: string | null;
}

export interface ComfyUIVersionRecord {
  tag: string;
  available_upstream: boolean;
  installed: boolean;
  active: boolean;
  locally_verified: boolean;
  failed_validation: boolean;
  failed_reason: string | null;
  source_hash: string | null;
  commit_sha: string | null;
  source_path: string | null;
  env_path: string | null;
  archive_url: string | null;
  installed_at: string | null;
  activated_at: string | null;
  validated_at: string | null;
  repair_status?: string | null;
  repair_attempt_count?: number;
  last_repair_attempt_at?: string | null;
  last_repair_error?: string | null;
  repair_blocked_until?: string | null;
  incompatible?: boolean;
  incompatible_reason?: string | null;
  last_successfully_started_at?: string | null;
}

export interface ComfyUIVersionOption {
  tag: string;
  label: string;
  status: string;
  available_upstream: boolean;
  installed: boolean;
  active: boolean;
  locally_verified: boolean;
  failed_validation: boolean;
  failed_reason: string | null;
  source_hash: string | null;
  commit_sha: string | null;
  published_at: string | null;
  repair_status?: string | null;
  repair_attempt_count?: number;
  last_repair_attempt_at?: string | null;
  last_repair_error?: string | null;
  repair_blocked_until?: string | null;
  incompatible?: boolean;
  incompatible_reason?: string | null;
}

export interface ComfyUIVersionsResponse {
  updates_allowed: boolean;
  disabled_reason: string | null;
  latest_tag: string | null;
  current: ComfyUIVersionRecord | null;
  options: ComfyUIVersionOption[];
  release_fetch_error: string | null;
}

export interface ComfyUIUpdateStatus {
  job_id: string | null;
  operation?: "update" | "repair" | string;
  phase: string;
  selected_version: string | null;
  resolved_tag: string | null;
  progress_label: string | null;
  status: string;
  error: string | null;
  installed_path: string | null;
  activated_version: string | null;
  repair_reason?: string | null;
  repair_attempt_count?: number | null;
  repair_blocked_until?: string | null;
  fallback_version?: string | null;
  incompatible_version?: string | null;
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

export function fetchComfyUIVersions() {
  return getJson<ComfyUIVersionsResponse>("/engine/comfyui/versions");
}

export function fetchComfyUILaunchSettings() {
  return getJson<ComfyUILaunchSettings>("/engine/comfyui/launch-settings");
}

export function fetchHealth() {
  return getJson<BackendHealthReport>("/health");
}

export function fetchWorkflows() {
  return getJson<WorkflowSummary[]>("/workflows");
}

// ─── Workflow package ────────────────────────────────────────────────────────

export interface WorkflowInputDef {
  id: string;
  label: string;
  control: string;
  binding: { node_id: string; input_name: string };
  default: unknown;
  validation: Record<string, unknown>;
}

export interface WorkflowOutputDef {
  id: string;
  label: string;
  node_id: string;
  type: string;
}

export interface DashboardControlDef {
  id: string;
  type: string;
  label: string;
  input_id?: string;
  output_id?: string;
  description?: string;
  group?: string;
  show_download?: boolean;
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface DashboardSectionDef {
  id: string;
  title: string;
  controls: DashboardControlDef[];
}

export interface DashboardSchemaDef {
  version: string;
  status: string;
  sections: DashboardSectionDef[];
}

export interface WorkflowPackageResponse {
  metadata: { id: string; name: string; version: string; description: string };
  inputs: WorkflowInputDef[];
  outputs: WorkflowOutputDef[];
  dashboard: DashboardSchemaDef;
  import_metadata?: { status: string };
}

export function fetchWorkflowPackage(workflowId: string): Promise<WorkflowPackageResponse> {
  return getJson<WorkflowPackageResponse>(`/workflows/${encodeURIComponent(workflowId)}/package`);
}

export function fetchTrustPolicy() {
  return getJson<TrustPolicyResponse>("/trust/policy");
}

export async function importWorkflowPackage(file: File, allowUnverifiedCommunityPreparation = false) {
  const data = await readFileAsArrayBuffer(file);
  const params = [`filename=${encodeURIComponent(file.name)}`];
  if (allowUnverifiedCommunityPreparation) {
    params.push("allow_unverified_community_preparation=true");
  }
  return postBytes<WorkflowImportResponse>(
    `/workflows/import?${params.join("&")}`,
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

export function fetchWorkflowStatus(workflowId: string) {
  return getJson<WorkflowStatusResponse>(`/workflows/${encodeURIComponent(workflowId)}/status`);
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
  return postJson<Record<string, unknown>>("/engine/comfyui/bootstrap");
}

export function startEngine() {
  return postJson<Record<string, unknown>>("/engine/comfyui/start");
}

export function stopEngine() {
  return postJson<Record<string, unknown>>("/engine/comfyui/stop");
}

export function updateComfyUI(version: string) {
  return postJson<ComfyUIUpdateStatus>("/engine/comfyui/update", { version });
}

export function rebuildComfyUI(version = "current") {
  return postJson<ComfyUIUpdateStatus>("/engine/comfyui/rebuild", { version });
}

export function fetchComfyUIUpdateStatus() {
  return getJson<ComfyUIUpdateStatus>("/engine/comfyui/update/status");
}

export function updateComfyUILaunchSettings(vramMode: ComfyUIVramMode) {
  return putJson<ComfyUILaunchSettingsUpdateResult>("/engine/comfyui/launch-settings", { vram_mode: vramMode });
}

export function isEngineJob(response: unknown): response is EngineJob {
  return Boolean(response && typeof response === "object" && "job_id" in response && "engine" in response);
}

// ─── Dashboard authoring ─────────────────────────────────────────────────────

export interface BindableInputEntry {
  input_name: string;
  current_value: unknown;
  kind: string;
  suggested_widget_type: string;
  widget_types: string[];
}

export interface BindableNode {
  node_id: string;
  node_type: string;
  is_image_node: boolean;
  is_lora_node: boolean;
  inputs: BindableInputEntry[];
}

export interface BindableInputsResponse {
  workflow_id: string;
  enrichment: "heuristic" | "object_info";
  nodes: BindableNode[];
}

export interface UnresolvedInput {
  node_id: string;
  node_type: string;
  input_name: string;
  current_value: unknown;
  reason: string;
}

export interface UnresolvedInputsResponse {
  workflow_id: string;
  unresolved_inputs: UnresolvedInput[];
}

export interface DashboardSavePayload {
  inputs: unknown[];
  dashboard: unknown;
}

export interface DashboardValidationResponse {
  workflow_id: string;
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface DashboardSaveResponse {
  workflow_id: string;
  status: string;
  valid: boolean;
  errors: string[];
  warnings: string[];
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "PUT",
    headers: apiHeaders("application/json"),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchBindableInputs(workflowId: string): Promise<BindableInputsResponse> {
  return getJson<BindableInputsResponse>(`/workflows/${encodeURIComponent(workflowId)}/bindable-inputs`);
}

export function fetchUnresolvedInputs(workflowId: string): Promise<UnresolvedInputsResponse> {
  return getJson<UnresolvedInputsResponse>(`/workflows/${encodeURIComponent(workflowId)}/unresolved-inputs`);
}

export function validateDashboard(
  workflowId: string,
  payload: DashboardSavePayload
): Promise<DashboardValidationResponse> {
  return postJson<DashboardValidationResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/dashboard/validate`,
    payload
  );
}

export function saveDashboard(
  workflowId: string,
  payload: DashboardSavePayload
): Promise<DashboardSaveResponse> {
  return putJson<DashboardSaveResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/dashboard`,
    payload
  );
}

export function exportWorkflowUrl(workflowId: string): string {
  return `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/export`;
}

// ─── User state ──────────────────────────────────────────────────────────────

export interface UserStateLayoutOverride {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface WorkflowUserState {
  schema_version: string;
  workflow_id: string;
  dashboard_version: string;
  values: Record<string, unknown>;
  layout_overrides: Record<string, UserStateLayoutOverride>;
}

async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "DELETE",
    headers: apiHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Noofy backend returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchUserState(workflowId: string): Promise<WorkflowUserState> {
  return getJson<WorkflowUserState>(`/workflows/${encodeURIComponent(workflowId)}/user-state`);
}

export function saveUserState(workflowId: string, state: WorkflowUserState): Promise<WorkflowUserState> {
  return putJson<WorkflowUserState>(`/workflows/${encodeURIComponent(workflowId)}/user-state`, state);
}

export function deleteUserStateValues(workflowId: string): Promise<WorkflowUserState> {
  return deleteJson<WorkflowUserState>(`/workflows/${encodeURIComponent(workflowId)}/user-state/values`);
}

export function deleteUserStateLayout(workflowId: string): Promise<WorkflowUserState> {
  return deleteJson<WorkflowUserState>(`/workflows/${encodeURIComponent(workflowId)}/user-state/layout`);
}

// ─── Dashboard assets ────────────────────────────────────────────────────────

export interface DashboardAssetUploadResponse {
  asset_id: string;
  original_filename: string;
}

export interface DashboardAssetMetadata {
  asset_id: string;
  original_filename: string;
  content_type: string;
}

export async function uploadDashboardAsset(
  workflowId: string,
  file: File,
): Promise<DashboardAssetUploadResponse> {
  const formData = new FormData();
  formData.append("image", file);
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/assets/image`,
    { method: "POST", headers, body: formData },
  );
  if (!response.ok) {
    throw new Error(`Asset upload failed: ${response.status}`);
  }
  return response.json() as Promise<DashboardAssetUploadResponse>;
}

export async function fetchAssetBlobUrl(assetId: string): Promise<string> {
  const token = getApiToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${getApiBaseUrl()}/assets/${encodeURIComponent(assetId)}`, { headers });
  if (!response.ok) throw new Error(`Asset not found: ${response.status}`);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export function fetchAssetMetadata(assetId: string): Promise<DashboardAssetMetadata> {
  return getJson<DashboardAssetMetadata>(`/assets/${encodeURIComponent(assetId)}/metadata`);
}

export async function uploadWorkflowImage(workflowId: string, file: File): Promise<{ filename: string }> {
  const formData = new FormData();
  formData.append("image", file);
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/uploads/image`,
    { method: "POST", headers, body: formData }
  );
  if (!response.ok) {
    throw new Error(`Image upload failed: ${response.status}`);
  }
  return response.json() as Promise<{ filename: string }>;
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
