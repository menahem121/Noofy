import { Box, Cpu, Hash, Maximize2, Package, SlidersHorizontal, Zap } from "lucide-react";

import type { ModelInventoryEntry, ModelInventorySource, ModelInventoryStatus } from "../../lib/api/noofyApi";

export type ModelTypeFilter = "all" | "checkpoint" | "lora" | "controlnet" | "upscaler" | "vae" | "embedding" | "other";

export const MODEL_TYPE_FILTERS: Array<{ id: ModelTypeFilter; label: string }> = [
  { id: "all", label: "All" },
  { id: "checkpoint", label: "Base models" },
  { id: "lora", label: "LoRAs" },
  { id: "controlnet", label: "ControlNet" },
  { id: "upscaler", label: "Upscalers" },
  { id: "vae", label: "VAE" },
  { id: "embedding", label: "Embeddings" },
  { id: "other", label: "Other" },
];

export const MODEL_TYPE_LABELS: Record<Exclude<ModelTypeFilter, "all">, string> = {
  checkpoint: "Base model",
  lora: "LoRA",
  controlnet: "ControlNet",
  upscaler: "Upscaler",
  vae: "VAE",
  embedding: "Embedding",
  other: "Other",
};

export const CATEGORY_LABELS: Record<string, string> = {
  checkpoints: "Base models",
  diffusion_models: "Base models",
  unet: "Base models",
  loras: "LoRAs",
  controlnet: "ControlNet",
  upscale_models: "Upscalers",
  vae: "VAE",
  embeddings: "Embeddings",
};

export const SOURCE_FILTERS: Array<{ id: "all" | ModelInventorySource; label: string }> = [
  { id: "all", label: "All sources" },
  { id: "noofy", label: "Noofy Models" },
  { id: "external_comfyui", label: "ComfyUI models folder" },
  { id: "engine_visible", label: "Other engine folders" },
  { id: "required_by_workflow", label: "Required by workflow" },
];

export const STATUS_LABELS: Record<ModelInventoryStatus, string> = {
  ready: "Ready",
  missing: "Missing",
  needs_attention: "Needs attention",
  never_used: "Never used",
};

export const TYPE_ICONS: Record<Exclude<ModelTypeFilter, "all">, typeof Box> = {
  checkpoint: Box,
  lora: Zap,
  controlnet: SlidersHorizontal,
  upscaler: Maximize2,
  vae: Cpu,
  embedding: Hash,
  other: Package,
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, " ");
}

export function folderNameLabel(folder: string): string {
  return folder.replace(/_/g, " ");
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function hexAlpha(hex: string, opacity: number): string {
  const alpha = Math.round(opacity * 255)
    .toString(16)
    .padStart(2, "0");
  return hex + alpha;
}

export function normalizeType(model: ModelInventoryEntry): Exclude<ModelTypeFilter, "all"> {
  const value = (model.model_type || model.folder).toLowerCase();
  if (value === "checkpoints" || value === "checkpoint" || value === "diffusion_models" || value === "unet") {
    return "checkpoint";
  }
  if (value === "loras" || value === "lora") return "lora";
  if (value === "controlnet") return "controlnet";
  if (value === "upscale_models" || value === "upscaler") return "upscaler";
  if (value === "vae") return "vae";
  if (value === "embeddings" || value === "embedding") return "embedding";
  return "other";
}

export function modelSourceLabel(model: ModelInventoryEntry): string {
  if (model.source !== "required_by_workflow") return model.source_label;

  const workflowNames = Array.from(
    new Set(model.workflow_usage.map((workflow) => workflow.workflow_name.trim()).filter(Boolean)),
  );
  if (workflowNames.length === 0) return model.source_label;
  if (workflowNames.length === 1) return `Required by ${workflowNames[0]}`;
  return `Required by ${workflowNames[0]} + ${workflowNames.length - 1} more`;
}

export function modelFolderPath(model: ModelInventoryEntry): string | null {
  if (!model.path) return model.matched_root;
  const index = Math.max(model.path.lastIndexOf("/"), model.path.lastIndexOf("\\"));
  return index > 0 ? model.path.slice(0, index) : model.matched_root;
}
