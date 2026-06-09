import {
  Image,
  Maximize2,
  PackageOpen,
  SlidersHorizontal,
  Sparkles,
  Video,
  type LucideIcon,
} from "lucide-react";

export const WORKFLOW_CATEGORY_OPTIONS = [
  "Txt2img",
  "Img2img",
  "txt2audio",
  "audio2audio",
  "txt2vid",
  "img2vid",
  "imgTo3D",
  "txtTo3D",
  "img2text",
  "audio2txt",
  "vid2vid",
  "Inpainting",
  "Outpainting",
  "Upscaling",
  "Style Transfer",
  "Swapping",
  "Character Consistency",
  "Pose Control",
  "Depth Control",
  "Canny / Line Control",
  "Background Replacement",
  "Background Removal",
  "Restoration",
  "All-in-one",
] as const;

export type WorkflowCategoryOption = (typeof WORKFLOW_CATEGORY_OPTIONS)[number];

export function workflowCategoryOption(value: string | null | undefined): WorkflowCategoryOption {
  return (WORKFLOW_CATEGORY_OPTIONS as readonly string[]).includes(value ?? "")
    ? value as WorkflowCategoryOption
    : WORKFLOW_CATEGORY_OPTIONS[0];
}

export const WORKFLOW_ICONS: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  image: Image,
  video: Video,
  maximize: Maximize2,
  sliders: SlidersHorizontal,
  package: PackageOpen,
};

export const NATIVE_WORKFLOW_ICON_OPTIONS = [
  { id: "sparkles", label: "Sparkles", Icon: Sparkles },
  { id: "image", label: "Image", Icon: Image },
  { id: "video", label: "Video", Icon: Video },
  { id: "maximize", label: "Upscale", Icon: Maximize2 },
  { id: "sliders", label: "Controls", Icon: SlidersHorizontal },
  { id: "package", label: "Package", Icon: PackageOpen },
] as const;
