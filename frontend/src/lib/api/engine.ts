import { getJson, postJson, putJson } from "./client";

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

export interface RuntimeStatus {
  backend_session_id?: string | null;
  backend_session_started_at?: string | null;
  mode: "external" | "managed";
  reachable: boolean;
  base_url: string;
  repo_dir: string;
  managed_process_running: boolean;
  sidecar_starting: boolean;
  environment_bootstrap_running?: boolean;
  environment_bootstrap_label?: string | null;
  pid: number | null;
  error: string | null;
  transient_health_failure?: boolean;
  last_reachable_at?: string | null;
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

export function fetchRuntimeStatus(options: { signal?: AbortSignal } = {}) {
  return getJson<RuntimeStatus>("/runtime", { cache: "no-store", signal: options.signal });
}

export function fetchResourceSnapshot() {
  return getJson<MachineResourceSnapshot>("/resources", { cache: "no-store" });
}

export function fetchHealth() {
  return getJson<BackendHealthReport>("/health");
}

export function fetchComfyUIVersions(options: { checkUpstream?: boolean } = {}) {
  const suffix = options.checkUpstream ? "?check_upstream=true" : "";
  return getJson<ComfyUIVersionsResponse>(`/engine/comfyui/versions${suffix}`);
}

export function fetchComfyUILaunchSettings() {
  return getJson<ComfyUILaunchSettings>("/engine/comfyui/launch-settings");
}

export function fetchComfyUIUpdateStatus() {
  return getJson<ComfyUIUpdateStatus>("/engine/comfyui/update/status");
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

export function updateComfyUILaunchSettings(vramMode: ComfyUIVramMode) {
  return putJson<ComfyUILaunchSettingsUpdateResult>("/engine/comfyui/launch-settings", { vram_mode: vramMode });
}
