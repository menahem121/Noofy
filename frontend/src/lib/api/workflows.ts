import { apiErrorMessage, apiHeaders, deleteJson, getApiBaseUrl, getApiToken, getJson, postBytes, postJson, putJson, resolveBackendUrl } from "./client";
import type { EngineJob } from "./jobs";

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

export type WorkflowHardwareWarningSeverity = "medium" | "high";
export type WorkflowHardwareWarningConfidence = "low" | "medium" | "high";
export type WorkflowHardwareWarningEstimateSource =
  | "declared"
  | "creator_observed"
  | "local_observed"
  | "heuristic"
  | "unknown";
export type WorkflowHardwareWarningReasonCode =
  | "local_memory_error"
  | "local_memory_error_settings_mismatch"
  | "local_success_settings_mismatch"
  | "temporary_low_free_memory"
  | "memory_pressure_high"
  | "estimated_vram_shortfall"
  | "estimated_ram_shortfall"
  | "estimated_vram_capacity_risk"
  | "estimated_ram_capacity_risk"
  | "creator_observed_memory_hint"
  | "model_size_heuristic";

export interface WorkflowHardwareWarningEstimate {
  estimated_peak_vram_mb: number | null;
  estimated_peak_ram_mb: number | null;
  source: WorkflowHardwareWarningEstimateSource;
  confidence: WorkflowHardwareWarningConfidence | null;
}

export interface WorkflowHardwareWarningMachineSignal {
  backend: string;
  memory_pressure: string;
  total_vram_mb: number | null;
  free_vram_mb: number | null;
  total_ram_mb: number | null;
  free_ram_mb: number | null;
  signal_quality: string;
}

export interface WorkflowHardwareWarningEvidence {
  local_successful_runs: number;
  local_memory_error_runs: number;
  local_input_profile_match: "matching" | "mismatched" | "none";
  creator_observation_available: boolean;
  model_size_heuristic_available: boolean;
  required_model_size_mb: number | null;
}

export interface WorkflowHardwareWarning {
  severity: WorkflowHardwareWarningSeverity;
  confidence: WorkflowHardwareWarningConfidence;
  exceeds_machine_capacity?: boolean;
  reason_codes: WorkflowHardwareWarningReasonCode[];
  estimate: WorkflowHardwareWarningEstimate;
  machine_signal: WorkflowHardwareWarningMachineSignal | null;
  evidence: WorkflowHardwareWarningEvidence;
  developer_details: Record<string, unknown>;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  display_name?: string;
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
  dashboard_status?: "configured" | "not_configured" | "invalid" | string;
  dashboard_ready?: boolean;
  unresolved_input_count?: number;
  custom_node_count?: number;
  required_model_count?: number;
  hardware_warning?: WorkflowHardwareWarning | null;
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
    display_name?: string;
    description: string;
    author: string;
    website: string;
    source: string;
    version: string;
  };
  models_used: WorkflowDetailsModel[];
  run_history: WorkflowRunHistorySummary;
  organization: {
    display_name?: string;
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
  display_name?: string;
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

export interface WorkflowOpenedResponse {
  workflow_id: string;
  last_opened: string | null;
  workflow: WorkflowSummary;
}

export interface WorkflowIconOption {
  id: string;
  asset_id?: string;
  label: string;
  kind: "custom";
  url: string;
}

export interface WorkflowIconsResponse {
  icons: WorkflowIconOption[];
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

export interface WorkflowInstallDeveloperDetailsResponse {
  workflow_id: string;
  developer_details: Record<string, unknown>;
}

export interface WorkflowRunnerLeaseResponse {
  workflow_id: string;
  status: string;
  lease_id: string | null;
  runner: Record<string, unknown> | null;
}

export interface QueuedRunnerStartCancelResponse {
  queue_id: string;
  workflow_id: string | null;
  status: string;
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
  user_errors?: RunUserFixableError[];
  error_category?: string | null;
  error_code?: string | null;
  developer_details?: Record<string, unknown>;
}

export interface RunUserFixableError {
  code: "missing_required_input" | "invalid_input_value" | string;
  title: string;
  message: string;
  user_message: string;
  severity: "user_fixable" | string;
  control_id: string | null;
  input_id: string | null;
  input_type: string | null;
  developer_details: Record<string, unknown>;
}

export interface OutputPreference {
  auto_save: boolean;
}

export type OutputPreferences = Record<string, OutputPreference>;

export interface WorkflowRunPayload {
  inputs: Record<string, unknown>;
  options?: Record<string, unknown>;
  output_preferences_snapshot?: OutputPreferences;
}

export type WorkflowRunResponse = EngineJob | WorkflowValidationResult;

export type RequiredModelStatus =
  | "available"
  | "possible_match"
  | "missing"
  | "checking"
  | "needs_manual_download"
  | "download_failed"
  | "verification_failed"
  | "authentication_required"
  | "access_denied"
  | "rate_limited"
  | "hash_mismatch"
  | "not_enough_disk_space"
  | "canceled";

export interface RequiredModelReference {
  requirement_id: string;
  node_id: string | null;
  node_type: string | null;
  input_name: string | null;
}

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
  references: RequiredModelReference[];
  reference_count: number;
  dedup_uncertain: boolean;
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
  status:
    | "pending"
    | "queued"
    | "running"
    | "downloading"
    | "verifying"
    | "succeeded"
    | "completed"
    | "failed"
    | "download_failed"
    | "verification_failed"
    | "authentication_required"
    | "access_denied"
    | "rate_limited"
    | "hash_mismatch"
    | "not_enough_disk_space"
    | "needs_manual_download"
    | "canceled"
    | string;
  status_label: string;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  message: string | null;
}

export interface ImportModelDownloadJobStatus {
  job_id: string;
  import_session_id: string;
  workflow_id: string;
  status:
    | "pending"
    | "queued"
    | "running"
    | "downloading"
    | "verifying"
    | "succeeded"
    | "completed"
    | "completed_with_errors"
    | "failed"
    | "canceled"
    | string;
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

export interface ImportModelVerificationJobStatus {
  job_id: string;
  import_session_id: string;
  workflow_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  user_facing_message: string;
  current_model_filename: string | null;
  current_model_index: number | null;
  total_models: number;
  verified_models: number;
  percent: number | null;
  models: RequiredModelAvailability[];
  model_summary: RequiredModelSummary | null;
}

export interface WorkflowModelVerificationJobStatus {
  job_id: string;
  workflow_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  user_facing_message: string;
  current_model_filename: string | null;
  current_model_index: number | null;
  total_models: number;
  verified_models: number;
  percent: number | null;
  models: RequiredModelAvailability[];
  model_summary: RequiredModelSummary | null;
}

export interface WorkflowImportResponse {
  import_session_id?: string | null;
  workflow_id: string;
  status:
    | "imported"
    | "needs_input_setup"
    | "cannot_prepare_automatically"
    | "missing_custom_nodes"
    | "needs_comfyui_update"
    | string;
  user_facing_message: string;
  workflow: WorkflowSummary;
  required_model_count: number;
  custom_node_count: number;
  unresolved_input_count: number;
  model_summary?: RequiredModelSummary | null;
  duplicate_identity?: {
    status: string;
    user_facing_message: string;
    existing_workflow?: WorkflowSummary;
    incoming_workflow?: WorkflowSummary;
    actions?: string[];
  } | null;
  custom_node_resolution?: {
    status: string;
    user_facing_message: string;
    unresolved_node_types: string[];
    ambiguous_node_types: Array<{ node_type: string; package_ids?: string[] }>;
    github_url_fields: Array<{ node_type: string; label: string }>;
    can_provide_github_urls: boolean;
    can_mark_no_custom_nodes: boolean;
    update_guidance?: string | null;
    developer_details?: Record<string, unknown>;
  } | null;
}

// ─── Workflow package ─────────────────────────────────────────────────────────

export interface WorkflowInputDef {
  id: string;
  label: string;
  control: string;
  binding: { node_id: string; input_name: string };
  default: unknown;
  default_pinned?: boolean;
  validation: Record<string, unknown>;
}

export interface WorkflowOutputDef {
  id: string;
  label: string;
  node_id: string;
  type: string;
  kind?: string | null;
}

export interface RequiredModelDef {
  folder: string;
  filename: string;
  node_id?: string | null;
  node_type?: string | null;
  input_name?: string | null;
  source_url?: string | null;
  checksum?: string | null;
  model_type?: string | null;
  size_bytes?: number | null;
  architecture_family?: string | null;
  architecture_family_confidence?: string | null;
  architecture_family_source?: string | null;
}

export interface DashboardControlDef {
  id: string;
  type: string;
  label: string;
  input_id?: string;
  output_id?: string;
  description?: string;
  provider?: string;
  required?: boolean;
  secret_ref?: string;
  injection_strategy?: { kind: string; field?: string | null };
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface DashboardControlGroupDef {
  id: string;
  title: string;
  description?: string;
  control_ids: string[];
  layout?: { x: number; y: number; w: number; h: number; min_w?: number; min_h?: number };
}

export interface DashboardActionBarPositionDef {
  x: number;
  y: number;
}

export interface DashboardPresentationDef {
  action_bar?: DashboardActionBarPositionDef | null;
}

export interface DashboardSectionDef {
  id: string;
  title: string;
  controls: DashboardControlDef[];
  groups?: DashboardControlGroupDef[];
}

export interface DashboardSchemaDef {
  version: string;
  status: string;
  presentation?: DashboardPresentationDef | null;
  sections: DashboardSectionDef[];
}

export interface WorkflowPackageResponse {
  metadata: {
    id: string;
    name: string;
    display_name?: string;
    version: string;
    description: string;
    author?: string;
    website?: string;
    category?: string;
    tags?: string[];
    icon?: string;
  };
  display_name?: string | null;
  identity?: Record<string, unknown> | null;
  required_models?: RequiredModelDef[];
  comfyui_graph?: Record<string, unknown>;
  inputs: WorkflowInputDef[];
  outputs: WorkflowOutputDef[];
  dashboard: DashboardSchemaDef;
  import_metadata?: { status: string };
}

// ─── Dashboard authoring ──────────────────────────────────────────────────────

export interface BindableInputEntry {
  input_name: string;
  current_value: unknown;
  kind: string;
  suggested_widget_type: string;
  widget_types: string[];
  options?: string[];
  hint?: string;
  auto_select?: boolean;
}

export interface BindableNode {
  node_id: string;
  node_type: string;
  node_title?: string;
  is_image_node: boolean;
  is_audio_node?: boolean;
  is_three_d_node?: boolean;
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

// ─── User state ───────────────────────────────────────────────────────────────

export interface UserStateLayoutOverride {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface UserStateActionBarPosition {
  x: number;
  y: number;
}

export interface UserStatePresentationOverrides {
  action_bar?: UserStateActionBarPosition | null;
}

export interface WorkflowUserState {
  schema_version: string;
  workflow_id: string;
  dashboard_version: string;
  values: Record<string, unknown>;
  layout_overrides: Record<string, UserStateLayoutOverride>;
  presentation_overrides?: UserStatePresentationOverrides;
  output_preferences: OutputPreferences;
}

// ─── Dashboard assets ─────────────────────────────────────────────────────────

export interface DashboardAssetUploadResponse {
  asset_id: string;
  original_filename: string;
}

export interface DashboardAssetMetadata {
  asset_id: string;
  original_filename: string;
  content_type: string;
  kind?: string;
  size?: number;
  format?: string;
  extension?: string;
  duration_seconds?: number;
  width?: number;
  height?: number;
  fps?: number;
  has_mask?: boolean;
  source_asset_id?: string;
  source_gallery_item_id?: string;
}

// ─── Workflow functions ───────────────────────────────────────────────────────

function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") {
    return file.arrayBuffer();
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) { resolve(reader.result); return; }
      reject(new Error("Workflow file could not be read."));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Workflow file could not be read."));
    reader.readAsArrayBuffer(file);
  });
}

export function fetchWorkflows(init: RequestInit = {}) {
  return getJson<WorkflowSummary[]>("/workflows", init);
}

export function fetchWorkflowDetails(workflowId: string) {
  return getJson<WorkflowDetails>(`/workflows/${encodeURIComponent(workflowId)}/details`);
}

export function recordWorkflowOpened(workflowId: string) {
  return postJson<WorkflowOpenedResponse>(`/workflows/${encodeURIComponent(workflowId)}/open`);
}

export function fetchWorkflowPackage(workflowId: string): Promise<WorkflowPackageResponse> {
  return getJson<WorkflowPackageResponse>(`/workflows/${encodeURIComponent(workflowId)}/package`);
}

export function fetchWorkflowStatus(workflowId: string) {
  return getJson<WorkflowStatusResponse>(`/workflows/${encodeURIComponent(workflowId)}/status`);
}

export function fetchWorkflowInstallDeveloperDetails(workflowId: string) {
  return getJson<WorkflowInstallDeveloperDetailsResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/install-state/developer-details`,
  );
}

export function fetchWorkflowModelSummary(workflowId: string) {
  return getJson<RequiredModelSummary>(`/workflows/${encodeURIComponent(workflowId)}/model-summary`);
}

export function startWorkflowModelVerification(workflowId: string) {
  return postJson<WorkflowModelVerificationJobStatus>(`/workflows/${encodeURIComponent(workflowId)}/model-verification`);
}

export function fetchWorkflowModelVerificationStatus(workflowId: string, jobId: string) {
  return getJson<WorkflowModelVerificationJobStatus>(
    `/workflows/${encodeURIComponent(workflowId)}/model-verification/${encodeURIComponent(jobId)}`,
  );
}

export function fetchTrustPolicy() {
  return getJson<TrustPolicyResponse>("/trust/policy");
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

export function validateWorkflow(workflowId: string) {
  return postJson<WorkflowValidationResult>(`/workflows/${workflowId}/validate`);
}

export function runWorkflow(workflowId: string, payload: WorkflowRunPayload) {
  return postJson<WorkflowRunResponse>(`/workflows/${workflowId}/run`, payload);
}

export interface WorkflowRunsCancelSummary {
  canceled_active_count: number;
  canceled_queued_count: number;
  already_terminal_count: number;
  failed_to_cancel_count: number;
}

export interface WorkflowActiveAndQueuedRunSummary {
  active_count: number;
  queued_count: number;
  total_count: number;
}

export function fetchWorkflowActiveAndQueuedRuns(workflowId: string) {
  return getJson<WorkflowActiveAndQueuedRunSummary>(
    `/workflows/${encodeURIComponent(workflowId)}/runs/active-and-queued`,
  );
}

export function cancelWorkflowActiveAndQueuedRuns(workflowId: string) {
  return postJson<WorkflowRunsCancelSummary>(
    `/workflows/${encodeURIComponent(workflowId)}/runs/cancel-active-and-queued`,
  );
}

export function openWorkflowRunnerLease(workflowId: string) {
  return postJson<WorkflowRunnerLeaseResponse>(`/workflows/${encodeURIComponent(workflowId)}/runner/leases`);
}

export function closeWorkflowRunnerLease(workflowId: string, leaseId: string) {
  return deleteJson<WorkflowRunnerLeaseResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/runner/leases/${encodeURIComponent(leaseId)}`,
  );
}

export function heartbeatWorkflowRunnerLease(workflowId: string, leaseId: string) {
  return putJson<WorkflowRunnerLeaseResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/runner/leases/${encodeURIComponent(leaseId)}/heartbeat`,
    {},
  );
}

export function closeWorkflowRunnerLeaseKeepalive(workflowId: string, leaseId: string) {
  void fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/runner/leases/${encodeURIComponent(leaseId)}`,
    {
      method: "DELETE",
      headers: apiHeaders(),
      keepalive: true,
    },
  ).catch(() => undefined);
}

export function cancelQueuedRunnerStart(queueId: string) {
  return deleteJson<QueuedRunnerStartCancelResponse>(`/workflows/runner/queue/${encodeURIComponent(queueId)}`);
}

export async function importWorkflowPackage(file: File, allowUnverifiedCommunityPreparation = false) {
  const data = await readFileAsArrayBuffer(file);
  const params = [`filename=${encodeURIComponent(file.name)}`];
  if (allowUnverifiedCommunityPreparation) params.push("allow_unverified_community_preparation=true");
  return postBytes<WorkflowImportResponse>(`/workflows/import?${params.join("&")}`, data);
}

export async function previewWorkflowPackageImport(file: File, allowUnverifiedCommunityPreparation = false) {
  const data = await readFileAsArrayBuffer(file);
  const params = [`filename=${encodeURIComponent(file.name)}`];
  if (allowUnverifiedCommunityPreparation) params.push("allow_unverified_community_preparation=true");
  return postBytes<WorkflowImportResponse>(`/workflows/import/preview?${params.join("&")}`, data);
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

export function fetchImportModelVerificationStatus(importSessionId: string) {
  return getJson<ImportModelVerificationJobStatus>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/model-verification`,
  );
}

export function cancelImportModelDownload(importSessionId: string, jobId: string) {
  return postJson<ImportModelDownloadJobStatus>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/download-models/${encodeURIComponent(jobId)}/cancel`,
  );
}

export function commitWorkflowImport(importSessionId: string, duplicateAction?: "replace" | "copy") {
  return postJson<WorkflowImportResponse>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/commit`,
    duplicateAction ? { duplicate_action: duplicateAction } : undefined,
  );
}

export function resolveImportCustomNodesFromUrls(
  importSessionId: string,
  urlsByNodeType: Record<string, string>,
) {
  return postJson<WorkflowImportResponse>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/custom-nodes/resolve-from-urls`,
    { urls_by_node_type: urlsByNodeType },
  );
}

export function markImportHasNoCustomNodes(importSessionId: string) {
  return postJson<WorkflowImportResponse>(
    `/workflows/import/${encodeURIComponent(importSessionId)}/custom-nodes/no-custom-nodes`,
  );
}

export function cancelWorkflowImport(importSessionId: string) {
  return deleteJson<{ import_session_id: string; status: string }>(
    `/workflows/import/${encodeURIComponent(importSessionId)}`,
  );
}

// ─── Dashboard authoring functions ───────────────────────────────────────────

export function fetchBindableInputs(workflowId: string): Promise<BindableInputsResponse> {
  return getJson<BindableInputsResponse>(`/workflows/${encodeURIComponent(workflowId)}/bindable-inputs`);
}

export function fetchUnresolvedInputs(workflowId: string): Promise<UnresolvedInputsResponse> {
  return getJson<UnresolvedInputsResponse>(`/workflows/${encodeURIComponent(workflowId)}/unresolved-inputs`);
}

export function validateDashboard(
  workflowId: string,
  payload: DashboardSavePayload,
): Promise<DashboardValidationResponse> {
  return postJson<DashboardValidationResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/dashboard/validate`,
    payload,
  );
}

export function saveDashboard(
  workflowId: string,
  payload: DashboardSavePayload,
): Promise<DashboardSaveResponse> {
  return putJson<DashboardSaveResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/dashboard`,
    payload,
  );
}

export function resetDashboardCustomization(workflowId: string): Promise<{ workflow_id: string; removed: boolean }> {
  return deleteJson<{ workflow_id: string; removed: boolean }>(
    `/workflows/${encodeURIComponent(workflowId)}/dashboard`,
  );
}

export function exportWorkflowUrl(workflowId: string): string {
  return resolveBackendUrl(`/workflows/${encodeURIComponent(workflowId)}/export`, { includeToken: true });
}

export function exportWorkflowComfyJsonUrl(workflowId: string): string {
  return resolveBackendUrl(`/workflows/${encodeURIComponent(workflowId)}/export/comfyui-json`, { includeToken: true });
}

// ─── User state functions ─────────────────────────────────────────────────────

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

// ─── Dashboard asset functions ────────────────────────────────────────────────

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
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<DashboardAssetUploadResponse>;
}

export async function copyGalleryImageToDashboardAsset(
  workflowId: string,
  inputId: string,
  galleryItemId: string,
): Promise<DashboardAssetUploadResponse> {
  return postJson<DashboardAssetUploadResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/assets/image/from-gallery`,
    { input_id: inputId, gallery_item_id: galleryItemId },
  );
}

export async function uploadDashboardImageMaskAsset(
  workflowId: string,
  sourceAssetId: string,
  mask: Blob,
): Promise<DashboardAssetUploadResponse> {
  const formData = new FormData();
  formData.append("source_asset_id", sourceAssetId);
  formData.append("mask", mask, "mask.png");
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/assets/image-mask`,
    { method: "POST", headers, body: formData },
  );
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<DashboardAssetUploadResponse>;
}

export interface UploadProgress {
  loaded: number;
  total: number | null;
  percent: number | null;
}

export async function uploadDashboardAudioAsset(
  workflowId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<DashboardAssetUploadResponse> {
  return uploadDashboardLargeMediaAsset("audio", workflowId, file, onProgress, signal);
}

export async function uploadDashboardVideoAsset(
  workflowId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<DashboardAssetUploadResponse> {
  return uploadDashboardLargeMediaAsset("video", workflowId, file, onProgress, signal);
}

export async function uploadDashboardThreeDAsset(
  workflowId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<DashboardAssetUploadResponse> {
  return uploadDashboardLargeMediaAsset("3d", workflowId, file, onProgress, signal);
}

export async function uploadDashboardFileAsset(
  workflowId: string,
  inputId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<DashboardAssetUploadResponse> {
  return uploadDashboardLargeMediaAsset("file", workflowId, file, onProgress, signal, inputId);
}

async function uploadDashboardLargeMediaAsset(
  kind: "audio" | "video" | "file" | "3d",
  workflowId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
  inputId?: string,
): Promise<DashboardAssetUploadResponse> {
  const formData = new FormData();
  formData.append(kind === "3d" ? "model" : kind, file);
  if (inputId) formData.append("input_id", inputId);
  const token = getApiToken();
  const url = `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/assets/${kind}`;
  const label = kind === "audio" ? "Audio" : kind === "video" ? "Video" : kind === "3d" ? "3D model" : "File";

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.setRequestHeader("Accept", "application/json");
    if (token) request.setRequestHeader("Authorization", `Bearer ${token}`);
    const abortRequest = () => request.abort();
    const cleanup = () => signal?.removeEventListener("abort", abortRequest);
    if (signal?.aborted) {
      reject(new Error(`${label} upload was canceled.`));
      return;
    }
    signal?.addEventListener("abort", abortRequest, { once: true });
    request.upload.onprogress = (event) => {
      const total = event.lengthComputable ? event.total : file.size || null;
      onProgress?.({
        loaded: event.loaded,
        total,
        percent: total ? Math.min(100, Math.round((event.loaded / total) * 100)) : null,
      });
    };
    request.onload = () => {
      cleanup();
      if (request.status >= 200 && request.status < 300) {
        try {
          resolve(JSON.parse(request.responseText) as DashboardAssetUploadResponse);
        } catch {
          reject(new Error(`Noofy returned an unexpected ${kind} upload response.`));
        }
        return;
      }
      reject(new Error(xhrErrorMessage(request)));
    };
    request.onerror = () => {
      cleanup();
      reject(new Error(`${label} upload failed.`));
    };
    request.onabort = () => {
      cleanup();
      reject(new Error(`${label} upload was canceled.`));
    };
    request.send(formData);
  });
}

function xhrErrorMessage(request: XMLHttpRequest): string {
  try {
    const payload = JSON.parse(request.responseText) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail;
  } catch {
    // Fall through to stable fallback.
  }
  return `Noofy reported an error while uploading this file (${request.status}).`;
}

export function fetchWorkflowIcons(): Promise<WorkflowIconsResponse> {
  return getJson<WorkflowIconsResponse>("/workflow-icons");
}

export async function uploadWorkflowIcon(file: File): Promise<WorkflowIconOption> {
  const formData = new FormData();
  formData.append("image", file);
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${getApiBaseUrl()}/workflow-icons`, { method: "POST", headers, body: formData });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<WorkflowIconOption>;
}

export function deleteWorkflowIcon(iconId: string): Promise<{ deleted: boolean; id: string }> {
  return deleteJson<{ deleted: boolean; id: string }>(`/workflow-icons/${encodeURIComponent(iconId)}`);
}

export async function fetchAssetBlobUrl(assetId: string): Promise<string> {
  const token = getApiToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${getApiBaseUrl()}/assets/${encodeURIComponent(assetId)}`, { headers });
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export function fetchAssetMetadata(assetId: string): Promise<DashboardAssetMetadata> {
  return getJson<DashboardAssetMetadata>(`/assets/${encodeURIComponent(assetId)}/metadata`);
}

export function dashboardAssetMediaUrl(assetId: string): string {
  return resolveBackendUrl(`/assets/${encodeURIComponent(assetId)}`, { includeToken: true });
}

export function workflowDefaultAssetMediaUrl(workflowId: string, inputId: string, assetId: string): string {
  return resolveBackendUrl(
    `/workflows/${encodeURIComponent(workflowId)}/inputs/${encodeURIComponent(inputId)}/default-asset?asset_id=${encodeURIComponent(assetId)}`,
    { includeToken: true },
  );
}

export async function uploadWorkflowImage(workflowId: string, file: File): Promise<{ filename: string }> {
  const formData = new FormData();
  formData.append("image", file);
  const token = getApiToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(
    `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowId)}/uploads/image`,
    { method: "POST", headers, body: formData },
  );
  if (!response.ok) throw new Error(await apiErrorMessage(response));
  return response.json() as Promise<{ filename: string }>;
}
