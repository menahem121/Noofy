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
  icon?: string;
  source_label?: string;
  main_model?: {
    name: string;
    type?: string | null;
    size_bytes?: number | null;
  } | null;
  category?: string;
  last_opened?: string | null;
  tags?: string[];
  missing_model_count?: number;
  needs_setup?: boolean;
  can_remove?: boolean;
  can_export_noofy?: boolean;
  can_export_comfyui_json?: boolean;
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

export interface WorkflowDetailsModel {
  name: string;
  type?: string | null;
  size_bytes?: number | null;
  status: string;
  status_label: string;
  folder?: string;
  source_path?: string | null;
}

export interface WorkflowRunHistorySummary {
  last_run_status: string | null;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_duration_seconds: number | null;
  average_duration_seconds: number | null;
  last_error: string | null;
  run_count: number;
}

export interface WorkflowDetails extends WorkflowSummary {
  overview: {
    description: string;
    author: string;
    website: string;
    source: string;
    version: string;
  };
  models_used: WorkflowDetailsModel[];
  run_history: WorkflowRunHistorySummary;
  organization: {
    category: string;
    tags: string[];
    icon: string;
  };
  advanced: {
    package_id: string;
    engine: string;
    trust_level: string;
    trust_label: string;
    can_export_noofy: boolean;
    can_export_comfyui_json: boolean;
    can_remove: boolean;
  };
}

export interface WorkflowMetadataUpdate {
  description?: string;
  author?: string;
  website?: string;
  category?: string;
  tags?: string[];
  icon?: string;
}

export interface WorkflowMetadataUpdateResponse {
  workflow_id: string;
  metadata: WorkflowMetadataUpdate;
  workflow: WorkflowSummary;
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
  sidecar_starting: boolean;
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
  model_paths?: Record<string, string | null>;
}

export interface ResourceMetric {
  available: boolean;
  percent: number | null;
  used_mb: number | null;
  total_mb: number | null;
  free_mb: number | null;
  source: string | null;
  error: string | null;
}

export interface MachineResourceSnapshot {
  observed_at: string;
  cpu: ResourceMetric;
  ram: ResourceMetric;
  vram: ResourceMetric;
  backend: string;
  device_name: string | null;
  memory_pressure: string;
}

export type ComfyUIVramMode = "normal" | "highvram" | "lowvram" | "novram" | "cpu";

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
  upstream_checked?: boolean;
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

export type ApiKeyProviderId = "hugging_face" | "civitai";

export interface ApiKeyProviderMetadata {
  provider: ApiKeyProviderId;
  label: string;
  configured: boolean;
  last_four: string | null;
}

export interface CredentialStoreStatus {
  available: boolean;
  status: string;
  error: string | null;
  kind?: string;
  backend?: string | null;
  display_path?: string | null;
  guidance?: string | null;
}

export interface ApiKeySettingsResponse {
  providers: Record<ApiKeyProviderId, ApiKeyProviderMetadata>;
  credential_store: CredentialStoreStatus;
}

export interface ApiKeyUpdateResult {
  status: "saved" | "cleared" | string;
  provider: ApiKeyProviderMetadata;
}

export interface ModelFolderSettings {
  noofy_models_dir: string;
  external_comfyui_models_dir: string | null;
  categories: string[];
  noofy_folder_exists: boolean;
  external_folder_exists: boolean | null;
}

export interface ModelFolderUpdateResult {
  status: string;
  settings: ModelFolderSettings;
  restart_required: boolean;
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
  model_type?: string | null;
  verification_level?: string | null;
  size_bytes?: number | null;
  source_urls?: string[];
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

export interface WorkflowRunPayload {
  inputs: Record<string, unknown>;
  options?: Record<string, unknown>;
  output_preferences_snapshot?: OutputPreferences;
}

export type WorkflowRunResponse = EngineJob | WorkflowValidationResult;

export interface WorkflowImportResponse {
  import_session_id?: string | null;
  workflow_id: string;
  status: "imported" | "needs_input_setup" | "cannot_prepare_automatically" | string;
  user_facing_message: string;
  workflow: WorkflowSummary;
  required_model_count: number;
  custom_node_count: number;
  unresolved_input_count: number;
  model_summary?: RequiredModelSummary | null;
}

export type RequiredModelStatus =
  | "available"
  | "possible_match"
  | "missing"
  | "needs_manual_download"
  | "download_failed"
  | "authentication_required"
  | "rate_limited"
  | "hash_mismatch"
  | "not_enough_disk_space";

export interface RequiredModelAvailability {
  requirement_id: string;
  node_id: string | null;
  node_type: string | null;
  input_name: string | null;
  filename: string;
  model_type: string | null;
  folder: string;
  verification_level: "sha256_size" | "filename_size" | "filename_only" | string;
  size_bytes: number | null;
  source_urls: string[];
  source_availability: "known" | "resolvable" | "unknown" | string;
  status: RequiredModelStatus;
  status_label: string;
  asset_ownership: string;
  source_path: string | null;
  matched_root: string | null;
  matched_sha256: string | null;
  matched_size_bytes: number | null;
  message: string | null;
}

export interface RequiredModelSummary {
  workflow_id: string;
  total_count: number;
  available_count: number;
  possible_match_count: number;
  missing_count: number;
  needs_manual_download_count: number;
  ready_to_run: boolean;
  models: RequiredModelAvailability[];
}

export interface ModelDownloadSummary {
  workflow_id: string;
  status: string;
  user_facing_message: string;
  downloaded_count: number;
  failed_count: number;
  model_summary: RequiredModelSummary;
}

export interface ImportModelDownloadJobStart {
  job_id: string;
  import_session_id: string;
  workflow_id: string;
  status: string;
  user_facing_message: string;
}

export interface ImportModelDownloadProgressItem {
  requirement_id: string;
  filename: string;
  status: "queued" | "downloading" | "verifying" | "completed" | "failed" | "canceled" | string;
  status_label: string;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  message: string | null;
}

export interface ImportModelDownloadJobStatus {
  job_id: string;
  import_session_id: string;
  workflow_id: string;
  status: "queued" | "running" | "completed" | "failed" | "canceled" | string;
  user_facing_message: string;
  current_model_filename: string | null;
  current_model_index: number | null;
  total_models: number;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  percent: number | null;
  speed_bytes_per_second: number | null;
  models: ImportModelDownloadProgressItem[];
  model_summary: RequiredModelSummary | null;
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

export function resolveBackendUrl(pathOrUrl: string, options: { includeToken?: boolean } = {}) {
  const apiBaseUrl = getApiBaseUrl();
  const isAbsoluteUrl = /^[a-z][a-z0-9+.-]*:/i.test(pathOrUrl) || pathOrUrl.startsWith("//");
  let resolved = pathOrUrl;
  let tokenEligible = !isAbsoluteUrl;

  if (!isAbsoluteUrl && pathOrUrl.startsWith(DEFAULT_API_BASE_URL)) {
    resolved =
      apiBaseUrl === DEFAULT_API_BASE_URL
        ? pathOrUrl
        : `${apiBaseUrl}${pathOrUrl.slice(DEFAULT_API_BASE_URL.length)}`;
  } else if (!isAbsoluteUrl && pathOrUrl.startsWith("/")) {
    resolved = `${apiBaseUrl}${pathOrUrl}`;
  } else if (!isAbsoluteUrl) {
    resolved = `${apiBaseUrl}/${pathOrUrl}`;
  } else if (apiBaseUrl !== DEFAULT_API_BASE_URL) {
    tokenEligible = pathOrUrl === apiBaseUrl || pathOrUrl.startsWith(`${apiBaseUrl}/`);
  }

  const token = options.includeToken ? getApiToken() : null;
  if (!token || !tokenEligible) {
    return resolved;
  }

  const separator = resolved.includes("?") ? "&" : "?";
  return `${resolved}${separator}token=${encodeURIComponent(token)}`;
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

export function fetchResourceSnapshot() {
  return getJson<MachineResourceSnapshot>("/resources");
}

export function fetchComfyUIVersions(options: { checkUpstream?: boolean } = {}) {
  const suffix = options.checkUpstream ? "?check_upstream=true" : "";
  return getJson<ComfyUIVersionsResponse>(`/engine/comfyui/versions${suffix}`);
}

export function fetchComfyUILaunchSettings() {
  return getJson<ComfyUILaunchSettings>("/engine/comfyui/launch-settings");
}

export function fetchApiKeySettings() {
  return getJson<ApiKeySettingsResponse>("/settings/apis");
}

export function fetchModelFolderSettings() {
  return getJson<ModelFolderSettings>("/settings/model-folders");
}

export function fetchHealth() {
  return getJson<BackendHealthReport>("/health");
}

export function fetchWorkflows() {
  return getJson<WorkflowSummary[]>("/workflows");
}

export function fetchWorkflowDetails(workflowId: string) {
  return getJson<WorkflowDetails>(`/workflows/${encodeURIComponent(workflowId)}/details`);
}

export function updateWorkflowMetadata(workflowId: string, payload: WorkflowMetadataUpdate) {
  return putJson<WorkflowMetadataUpdateResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/metadata`,
    payload,
  );
}

export function removeWorkflow(workflowId: string) {
  return deleteJson<{ workflow_id: string; removed: boolean }>(`/workflows/${encodeURIComponent(workflowId)}`);
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

export async function previewWorkflowPackageImport(file: File, allowUnverifiedCommunityPreparation = false) {
  const data = await readFileAsArrayBuffer(file);
  const params = [`filename=${encodeURIComponent(file.name)}`];
  if (allowUnverifiedCommunityPreparation) {
    params.push("allow_unverified_community_preparation=true");
  }
  return postBytes<WorkflowImportResponse>(
    `/workflows/import/preview?${params.join("&")}`,
    data,
  );
}

export function downloadImportMissingModels(importSessionId: string) {
  return postJson<ImportModelDownloadJobStart>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/download-models`,
  );
}

export function fetchImportModelDownloadStatus(importSessionId: string, jobId: string) {
  return getJson<ImportModelDownloadJobStatus>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/download-models/${encodeURIComponent(jobId)}`,
  );
}

export function cancelImportModelDownload(importSessionId: string, jobId: string) {
  return postJson<ImportModelDownloadJobStatus>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/download-models/${encodeURIComponent(jobId)}/cancel`,
  );
}

export function commitWorkflowImport(importSessionId: string) {
  return postJson<WorkflowImportResponse>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/commit`,
  );
}

export function cancelWorkflowImport(importSessionId: string) {
  return deleteJson<{ import_session_id: string; status: string }>(
    `/workflows/import/${encodeURIComponent(importSessionId)}`,
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

export function fetchWorkflowModelSummary(workflowId: string) {
  return getJson<RequiredModelSummary>(`/workflows/${encodeURIComponent(workflowId)}/model-summary`);
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

export function updateExternalApiKey(provider: ApiKeyProviderId, apiKey: string) {
  return putJson<ApiKeyUpdateResult>(`/settings/apis/${provider}/key`, { api_key: apiKey });
}

export function clearExternalApiKey(provider: ApiKeyProviderId) {
  return deleteJson<ApiKeyUpdateResult>(`/settings/apis/${provider}/key`);
}

export function updateModelFolderSettings(payload: {
  noofy_models_dir?: string;
  external_comfyui_models_dir?: string;
}) {
  return putJson<ModelFolderUpdateResult>("/settings/model-folders", payload);
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
  options?: string[];
  hint?: string;
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

export function exportWorkflowComfyJsonUrl(workflowId: string): string {
  return `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/export/comfyui-json`;
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
  output_preferences: OutputPreferences;
}

export interface OutputPreference {
  auto_save: boolean;
}

export type OutputPreferences = Record<string, OutputPreference>;

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
export type GalleryGenerationSettings = Record<string, unknown>;

export interface GalleryImage {
  id: string;
  /** URL or relative path for the grid thumbnail (may be same as imageUrl) */
  thumbnailUrl: string;
  /** URL or relative path for the full-resolution image */
  imageUrl: string;
  fileState: "available" | "missing" | "degraded" | string;
  workflowId: string;
  workflowName: string;
  /** The text prompt the user typed, if applicable */
  prompt: string;
  /** ISO-8601 timestamp */
  createdAt: string;
  width: number | null;
  height: number | null;
  favorite: boolean;
  widgetTitle?: string;
  mimeType?: string | null;
  /**
   * User-facing workflow widget values at the time of generation.
   * Only values the user could edit in the Noofy workflow UI.
   */
  usedSettings: GalleryImageSettings;
  generationSettings?: GalleryGenerationSettings;
  /** Backend file reference (path or output ref) — not shown in default UI */
  fileRef: string;
}

export interface GalleryResponse {
  images: GalleryImage[];
  total: number;
}

export async function fetchGallery(): Promise<GalleryResponse> {
  const data = await getJson<{ images: unknown[]; total: number }>("/gallery");
  return {
    images: Array.isArray(data.images) ? data.images.map(normalizeGalleryImage) : [],
    total: data.total,
  };
}

export async function fetchGalleryItem(itemId: string): Promise<GalleryImage> {
  return normalizeGalleryImage(await getJson<unknown>(`/gallery/${encodeURIComponent(itemId)}`));
}

export async function updateGalleryFavorite(itemId: string, favorite: boolean): Promise<GalleryImage> {
  return normalizeGalleryImage(await putJson<unknown>(`/gallery/${encodeURIComponent(itemId)}/favorite`, { favorite }));
}

export function deleteGalleryItem(itemId: string): Promise<{ id: string; deleted: boolean }> {
  return deleteJson<{ id: string; deleted: boolean }>(`/gallery/${encodeURIComponent(itemId)}`);
}

function normalizeGalleryImage(raw: unknown): GalleryImage {
  const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const generationSettings =
    item.generation_settings && typeof item.generation_settings === "object"
      ? item.generation_settings as Record<string, unknown>
      : item.generationSettings && typeof item.generationSettings === "object"
        ? item.generationSettings as Record<string, unknown>
        : {};
  const usedSettings =
    generationSettings.settings && typeof generationSettings.settings === "object"
      ? generationSettings.settings as GalleryImageSettings
      : item.usedSettings && typeof item.usedSettings === "object"
        ? item.usedSettings as GalleryImageSettings
        : {};
  const prompt = typeof usedSettings.Prompt === "string"
    ? usedSettings.Prompt
    : typeof usedSettings.prompt === "string"
      ? usedSettings.prompt
      : typeof item.prompt === "string"
        ? item.prompt
        : "";
  const imageUrl = String(item.image_url ?? item.imageUrl ?? "");
  const fileState = String(item.file_state ?? item.fileState ?? "available");
  const thumbnailUrl = String(item.thumbnail_url ?? item.thumbnailUrl ?? imageUrl);
  return {
    id: String(item.id ?? ""),
    thumbnailUrl: (fileState === "degraded" ? imageUrl : thumbnailUrl)
      ? resolveBackendUrl(fileState === "degraded" ? imageUrl : thumbnailUrl, { includeToken: true })
      : "",
    imageUrl: imageUrl ? resolveBackendUrl(imageUrl, { includeToken: true }) : "",
    fileState,
    workflowId: String(item.workflow_id ?? item.workflowId ?? ""),
    workflowName: String(item.workflow_title ?? item.workflowName ?? ""),
    prompt,
    createdAt: String(item.created_at ?? item.createdAt ?? ""),
    width: typeof item.width === "number" ? item.width : null,
    height: typeof item.height === "number" ? item.height : null,
    favorite: Boolean(item.favorite),
    widgetTitle: typeof item.widget_title === "string" ? item.widget_title : undefined,
    mimeType: typeof item.mime_type === "string" ? item.mime_type : null,
    usedSettings,
    generationSettings,
    fileRef: String(item.image_rel_path ?? item.fileRef ?? ""),
  };
}
