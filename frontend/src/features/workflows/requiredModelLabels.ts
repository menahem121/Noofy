const MODEL_FOLDER_TYPE_LABELS: Record<string, string> = {
  clip: "CLIP",
  clip_vision: "CLIP Vision",
  checkpoints: "Checkpoint",
  controlnet: "ControlNet",
  diffusion_models: "Diffusion model",
  embeddings: "Embedding",
  gligen: "GLIGEN",
  llm: "LLM",
  loras: "LoRA",
  onnx: "ONNX",
  rmbg: "RMBG",
  sams: "SAM",
  unet: "UNet",
  upscale_models: "Upscale model",
  vae: "VAE",
  vae_approx: "Approximate VAE",
  yolo: "YOLO",
};

export function requiredModelTypeLabel(folder: string, modelType?: string | null): string {
  const normalizedFolder = folder.trim().toLowerCase();
  if (normalizedFolder) {
    return MODEL_FOLDER_TYPE_LABELS[normalizedFolder] ?? humanizeLabel(normalizedFolder);
  }
  return humanizeLabel(modelType ?? "") || "Model";
}

function humanizeLabel(value: string): string {
  const words = value
    .trim()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
  if (!words) return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}
