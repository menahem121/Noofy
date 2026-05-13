import { deleteJson, getJson, postJson, putJson } from "./client";
import type { ImportModelDownloadProgressItem } from "./workflows";

export type ModelInventoryStatus = "ready" | "missing" | "needs_attention";
export type ModelInventorySource = "noofy" | "external_comfyui" | "engine_visible" | "required_by_workflow";
export type ModelOwnership =
  | "noofy_downloaded"
  | "noofy_imported"
  | "noofy_local"
  | "external_reference"
  | "engine_reference"
  | "workflow_requirement";

export interface ModelTag {
  id: string;
  name: string;
  color: string;
}

export interface ModelWorkflowReference {
  workflow_id: string;
  workflow_name: string;
  requirement_id: string;
  status: string;
  status_label: string;
}

export interface ModelDownloadReference {
  workflow_id: string;
  workflow_name: string;
  requirement_id: string;
}

export interface ModelInventoryEntry {
  model_key: string;
  filename: string;
  folder: string;
  model_type: string;
  size_bytes: number | null;
  status: ModelInventoryStatus;
  status_label: string;
  source: ModelInventorySource;
  source_label: string;
  ownership: ModelOwnership;
  ownership_label: string;
  can_delete: boolean;
  delete_unavailable_reason: string | null;
  path: string | null;
  matched_root: string | null;
  verification_level: string | null;
  matched_sha256: string | null;
  source_availability: string | null;
  message: string | null;
  workflow_usage: ModelWorkflowReference[];
  downloadable_references: ModelDownloadReference[];
  tag_ids: string[];
}

export interface ModelInventoryResponse {
  schema_version?: string;
  summary: {
    total_count: number;
    noofy_count: number;
    external_comfyui_count: number;
    missing_count: number;
    total_known_size_bytes: number;
  };
  folders: {
    noofy_models_dir: string;
    external_comfyui_models_dir: string | null;
    categories: string[];
  };
  tags: ModelTag[];
  models: ModelInventoryEntry[];
}

export interface ModelImportItemResult {
  source_path: string;
  filename: string | null;
  target_path: string | null;
  status: "imported" | "already_in_place" | "failed";
  message: string | null;
}

export interface ModelImportResponse {
  status: "completed" | "completed_with_errors";
  imported_count: number;
  failed_count: number;
  models: ModelImportItemResult[];
}

export interface ModelDownloadSelection {
  workflow_id: string;
  requirement_id: string;
}

export interface ModelDownloadJobStart {
  job_id: string;
  status: string;
  user_facing_message: string;
}

export interface ModelDownloadJobStatus {
  job_id: string;
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
}

export interface ModelDownloadActiveResponse {
  job: ModelDownloadJobStatus | null;
}

export function fetchModelInventory() {
  return getJson<ModelInventoryResponse>("/models");
}

export function importModelFiles(payload: { source_paths: string[]; folder: string; overwrite?: boolean }) {
  return postJson<ModelImportResponse>("/models/import", payload);
}

export function createModelTag(payload: { name: string; color: string }) {
  return postJson<ModelTag>("/models/tags", payload);
}

export function updateModelTags(modelKey: string, tagIds: string[]) {
  return putJson<{ model_key: string; tag_ids: string[] }>(
    `/models/${encodeURIComponent(modelKey)}/tags`,
    { tag_ids: tagIds },
  );
}

export function startModelDownload(selections: ModelDownloadSelection[]) {
  return postJson<ModelDownloadJobStart>("/models/downloads", { selections });
}

export function fetchModelDownloadStatus(jobId: string) {
  return getJson<ModelDownloadJobStatus>(`/models/downloads/${encodeURIComponent(jobId)}`);
}

export function fetchActiveModelDownload() {
  return getJson<ModelDownloadActiveResponse>("/models/downloads/active");
}

export function cancelModelDownload(jobId: string) {
  return postJson<ModelDownloadJobStatus>(`/models/downloads/${encodeURIComponent(jobId)}/cancel`);
}

export function deleteModelFile(modelKey: string) {
  return deleteJson<{ model_key: string; deleted: boolean; message: string }>(`/models/${encodeURIComponent(modelKey)}`);
}
