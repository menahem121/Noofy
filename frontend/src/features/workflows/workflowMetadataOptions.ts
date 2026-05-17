import {
  Image,
  Maximize2,
  PackageOpen,
  SlidersHorizontal,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export const WORKFLOW_CATEGORY_OPTIONS = [
  "Txt2img",
  "Img2img",
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

export const WORKFLOW_ICONS: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  image: Image,
  maximize: Maximize2,
  sliders: SlidersHorizontal,
  package: PackageOpen,
};

export const NATIVE_WORKFLOW_ICON_OPTIONS = [
  { id: "sparkles", label: "Sparkles", Icon: Sparkles },
  { id: "image", label: "Image", Icon: Image },
  { id: "maximize", label: "Upscale", Icon: Maximize2 },
  { id: "sliders", label: "Controls", Icon: SlidersHorizontal },
  { id: "package", label: "Package", Icon: PackageOpen },
] as const;
