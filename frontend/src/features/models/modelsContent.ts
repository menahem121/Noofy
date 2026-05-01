export type ModelType = "checkpoint" | "lora" | "controlnet" | "upscaler" | "vae" | "embedding" | "other";
export type ModelStatus = "ready" | "missing" | "needs_attention";
export type ModelSource = "downloaded" | "imported" | "linked";

export interface ModelTag {
  id: string;
  name: string;
  color: string;
}

export interface ModelEntry {
  id: string;
  name: string;
  type: ModelType;
  tagIds: string[];
  sizeBytes: number;
  status: ModelStatus;
  lastUsed: string | null;
  source: ModelSource;
  usedByWorkflows: string[];
  filePath: string;
  hash: string | null;
  comfyFolder: string;
}

export const MODEL_TYPE_LABELS: Record<ModelType, string> = {
  checkpoint: "Checkpoint",
  lora: "LoRA",
  controlnet: "ControlNet",
  upscaler: "Upscaler",
  vae: "VAE",
  embedding: "Embedding",
  other: "Other",
};

export const MODEL_STATUS_LABELS: Record<ModelStatus, string> = {
  ready: "Ready",
  missing: "Missing",
  needs_attention: "Needs attention",
};

export const MODEL_SOURCE_LABELS: Record<ModelSource, string> = {
  downloaded: "Downloaded by Noofy",
  imported: "Imported into Noofy",
  linked: "Linked from your computer",
};

export const INITIAL_TAGS: ModelTag[] = [
  { id: "tag_sdxl", name: "SDXL", color: "#8b5cf6" },
  { id: "tag_sd15", name: "SD 1.5", color: "#60a5fa" },
  { id: "tag_realistic", name: "Realistic", color: "#4ade80" },
  { id: "tag_anime", name: "Anime", color: "#f472b6" },
  { id: "tag_style", name: "Style", color: "#fb923c" },
];

export const TAG_COLOR_PRESETS = [
  "#8b5cf6",
  "#60a5fa",
  "#4ade80",
  "#fbbf24",
  "#f87171",
  "#f472b6",
  "#fb923c",
  "#2dd4bf",
];

export const MOCK_MODELS: ModelEntry[] = [
  {
    id: "sd_xl_base_1.0",
    name: "SDXL Base 1.0",
    type: "checkpoint",
    tagIds: ["tag_sdxl"],
    sizeBytes: 6_938_008_576,
    status: "ready",
    lastUsed: "2 days ago",
    source: "downloaded",
    usedByWorkflows: ["Text to Image XL", "Portrait Generator"],
    filePath: "~/Noofy/models/checkpoints/sd_xl_base_1.0.safetensors",
    hash: "31e35c80fc4829d14f90153f4c74cd59c90b779f5eecea",
    comfyFolder: "checkpoints",
  },
  {
    id: "dreamshaper_8",
    name: "DreamShaper V8",
    type: "checkpoint",
    tagIds: ["tag_sd15", "tag_realistic"],
    sizeBytes: 2_132_230_144,
    status: "ready",
    lastUsed: "5 days ago",
    source: "imported",
    usedByWorkflows: ["Realistic Portrait"],
    filePath: "~/Noofy/models/checkpoints/dreamshaper_8.safetensors",
    hash: "879db523c30d3b9017143d56705015e15a2cb5e5a748",
    comfyFolder: "checkpoints",
  },
  {
    id: "detail_tweaker_xl",
    name: "Detail Tweaker XL",
    type: "lora",
    tagIds: ["tag_sdxl", "tag_style"],
    sizeBytes: 151_552_000,
    status: "ready",
    lastUsed: "2 days ago",
    source: "downloaded",
    usedByWorkflows: ["Text to Image XL"],
    filePath: "~/Noofy/models/loras/detail_tweaker_xl.safetensors",
    hash: "a1b2c3d4e5f6789abcdef012345",
    comfyFolder: "loras",
  },
  {
    id: "controlnet_canny_sdxl",
    name: "ControlNet Canny XL",
    type: "controlnet",
    tagIds: ["tag_sdxl"],
    sizeBytes: 2_631_892_992,
    status: "missing",
    lastUsed: null,
    source: "downloaded",
    usedByWorkflows: ["Sketch to Image"],
    filePath: "~/Noofy/models/controlnet/controlnet_canny_sdxl.safetensors",
    hash: null,
    comfyFolder: "controlnet",
  },
  {
    id: "vae_sdxl",
    name: "SDXL VAE",
    type: "vae",
    tagIds: ["tag_sdxl"],
    sizeBytes: 335_544_320,
    status: "ready",
    lastUsed: "2 days ago",
    source: "downloaded",
    usedByWorkflows: ["Text to Image XL"],
    filePath: "~/Noofy/models/vae/sdxl_vae.safetensors",
    hash: "vae_hash_abc123",
    comfyFolder: "vae",
  },
  {
    id: "real_esrgan_x4",
    name: "RealESRGAN x4+",
    type: "upscaler",
    tagIds: [],
    sizeBytes: 67_108_864,
    status: "ready",
    lastUsed: "1 week ago",
    source: "downloaded",
    usedByWorkflows: ["Image Upscaler"],
    filePath: "~/Noofy/models/upscale_models/RealESRGAN_x4plus.pth",
    hash: "upscaler_hash_def456",
    comfyFolder: "upscale_models",
  },
  {
    id: "badhands_v4",
    name: "BadHands v4",
    type: "embedding",
    tagIds: ["tag_sd15"],
    sizeBytes: 16_384,
    status: "ready",
    lastUsed: "1 month ago",
    source: "linked",
    usedByWorkflows: [],
    filePath: "/Users/user/Downloads/badhands_v4.pt",
    hash: null,
    comfyFolder: "embeddings",
  },
  {
    id: "controlnet_depth",
    name: "ControlNet Depth",
    type: "controlnet",
    tagIds: [],
    sizeBytes: 1_469_235_200,
    status: "missing",
    lastUsed: null,
    source: "downloaded",
    usedByWorkflows: ["3D Scene Generator"],
    filePath: "~/Noofy/models/controlnet/depth.safetensors",
    hash: null,
    comfyFolder: "controlnet",
  },
];
