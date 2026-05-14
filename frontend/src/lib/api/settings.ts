import { deleteJson, getJson, putJson } from "./client";

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

export function fetchApiKeySettings() {
  return getJson<ApiKeySettingsResponse>("/settings/apis");
}

export function fetchModelFolderSettings() {
  return getJson<ModelFolderSettings>("/settings/model-folders");
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
