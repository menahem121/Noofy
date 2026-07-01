import { deleteJson, getJson, postJson, putJson } from "./client";

export type ApiKeyProviderId = "hugging_face" | "civitai" | "comfy_org";

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

export interface OnboardingState {
  schema_version: string;
  completed: boolean;
  completed_at: string | null;
}

export interface OnboardingUpdateResult {
  status: "completed" | "already_completed" | string;
  onboarding: OnboardingState;
}

export interface NoofyRuntimeReleaseInfo {
  tag: string;
  name: string | null;
  published_at: string | null;
  html_url: string | null;
  asset_name: string;
  asset_url: string;
  asset_sha256: string;
  asset_size: number | null;
  checked_at: string;
}

export interface NoofyRuntimeRecord {
  runtime_id: string;
  tag: string;
  target: string;
  runtime_path: string;
  manifest_sha256: string;
  backend_sha256: string | null;
  python_version: string | null;
  uv_version: string | null;
  asset_name: string | null;
  asset_url: string | null;
  asset_sha256: string | null;
  staged_at: string | null;
  activated_at: string | null;
}

export interface NoofyRuntimeSettingsResponse {
  available: boolean;
  disabled_reason: string | null;
  packaged_runtime: boolean;
  developer_override: boolean;
  update_repo: string | null;
  target: string | null;
  current_version: string | null;
  current_runtime_id: string | null;
  current_runtime_path: string | null;
  current_source: string;
  latest: NoofyRuntimeReleaseInfo | null;
  pending: NoofyRuntimeRecord | null;
  active: NoofyRuntimeRecord | null;
}

export interface NoofyRuntimeCheckResult {
  status: string;
  latest: NoofyRuntimeReleaseInfo | null;
  disabled_reason: string | null;
}

export interface NoofyRuntimeUpdateStatus {
  job_id: string | null;
  phase: string;
  status: string;
  progress_label: string | null;
  latest_version: string | null;
  staged_runtime_id: string | null;
  error: string | null;
}

export interface NoofyRuntimeActivateResult {
  status: string;
  active: NoofyRuntimeRecord | null;
  disabled_reason: string | null;
  error: string | null;
}

export interface LocalEngineFilesRemoveResult {
  status: string;
  bytes_deleted: number;
  deleted_paths: Array<{ path: string; bytes_deleted: number }>;
  skipped_paths: string[];
  preserved_paths: {
    models: string;
    outputs: string;
    workflows: string;
  };
}

export function fetchApiKeySettings() {
  return getJson<ApiKeySettingsResponse>("/settings/apis");
}

export function fetchOnboardingState() {
  return getJson<OnboardingState>("/settings/onboarding");
}

export function completeOnboarding() {
  return putJson<OnboardingUpdateResult>("/settings/onboarding", {});
}

export function fetchModelFolderSettings() {
  return getJson<ModelFolderSettings>("/settings/model-folders");
}

export function fetchNoofyRuntimeSettings() {
  return getJson<NoofyRuntimeSettingsResponse>("/settings/noofy-runtime");
}

export function checkNoofyRuntimeUpdate() {
  return postJson<NoofyRuntimeCheckResult>("/settings/noofy-runtime/check");
}

export function stageNoofyRuntimeUpdate() {
  return postJson<NoofyRuntimeUpdateStatus>("/settings/noofy-runtime/stage");
}

export function fetchNoofyRuntimeUpdateStatus() {
  return getJson<NoofyRuntimeUpdateStatus>(
    "/settings/noofy-runtime/update/status",
  );
}

export function activateNoofyRuntimeUpdate() {
  return postJson<NoofyRuntimeActivateResult>(
    "/settings/noofy-runtime/activate",
  );
}

export function removeLocalEngineFiles() {
  return deleteJson<LocalEngineFilesRemoveResult>("/settings/local-engine-files");
}

export function updateExternalApiKey(
  provider: ApiKeyProviderId,
  apiKey: string,
) {
  return putJson<ApiKeyUpdateResult>(`/settings/apis/${provider}/key`, {
    api_key: apiKey,
  });
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
