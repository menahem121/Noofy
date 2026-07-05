import { getJson, postJson } from "./client";
import type { ModelDownloadJobStart } from "./models";
import type { RequiredModelSummary, WorkflowValidationResult } from "./workflows";

export const FP8_INCOMPATIBLE_MPS_ERROR_CODE = "fp8_incompatible_mps";

export interface Fp8IncompatibleModel {
  folder: string;
  filename: string;
  fp8_dtypes?: string[];
  quant_format?: string;
}

export interface Fp8ConversionJobStart {
  job_id: string;
  status: string;
  user_facing_message: string | null;
}

export interface Fp8ConversionJobStatus {
  job_id: string;
  workflow_id: string;
  folder: string;
  filename: string;
  status: "queued" | "converting" | "finalizing" | "completed" | "failed" | "canceled" | string;
  percent: number | null;
  user_facing_message: string | null;
  error_code: string | null;
  converted_filename: string | null;
  target_dtype: string | null;
  source_removed: boolean | null;
  source_removal_skipped_reason: string | null;
  model_summary: RequiredModelSummary | null;
}

export function isFp8IncompatibleValidation(response: WorkflowValidationResult): boolean {
  return response.error_code === FP8_INCOMPATIBLE_MPS_ERROR_CODE;
}

export function fp8ModelsFromValidation(response: WorkflowValidationResult): Fp8IncompatibleModel[] {
  const details = response.developer_details;
  const models = details && (details as { fp8_models?: unknown }).fp8_models;
  if (!Array.isArray(models)) {
    return [];
  }
  return models.filter(
    (model): model is Fp8IncompatibleModel =>
      Boolean(model) &&
      typeof (model as Fp8IncompatibleModel).folder === "string" &&
      typeof (model as Fp8IncompatibleModel).filename === "string",
  );
}

export function startFp8Conversion(workflowId: string, model: { folder: string; filename: string }) {
  return postJson<Fp8ConversionJobStart>(
    `/workflows/${encodeURIComponent(workflowId)}/fp8-compatibility/convert`,
    model,
  );
}

export function fetchFp8ConversionStatus(workflowId: string, jobId: string) {
  return getJson<Fp8ConversionJobStatus>(
    `/workflows/${encodeURIComponent(workflowId)}/fp8-compatibility/convert/${encodeURIComponent(jobId)}`,
  );
}

export function cancelFp8Conversion(workflowId: string, jobId: string) {
  return postJson<Fp8ConversionJobStatus>(
    `/workflows/${encodeURIComponent(workflowId)}/fp8-compatibility/convert/${encodeURIComponent(jobId)}/cancel`,
    {},
  );
}

export function startFp8AlternativeDownload(
  workflowId: string,
  payload: { folder: string; filename: string; url: string },
) {
  return postJson<ModelDownloadJobStart>(
    `/workflows/${encodeURIComponent(workflowId)}/fp8-compatibility/download`,
    payload,
  );
}

export function dismissFp8Compatibility(
  workflowId: string,
  model: { folder: string; filename: string },
) {
  return postJson<{ status: string }>(
    `/workflows/${encodeURIComponent(workflowId)}/fp8-compatibility/dismiss`,
    model,
  );
}

export function isValidFp8AlternativeUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || !parsed.hostname) {
    return false;
  }
  // Only same-container variants keep the graph's loader nodes working
  // (.sft is the same safetensors container under a shorter name).
  const filename = decodeURIComponent(parsed.pathname.split("/").pop() ?? "");
  return /\.(safetensors|sft)$/i.test(filename);
}
