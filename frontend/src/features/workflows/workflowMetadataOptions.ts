import { createElement, forwardRef } from "react";
import {
  Box,
  Brush,
  FileAudio,
  FileText,
  Image,
  Maximize2,
  PackageOpen,
  Scaling,
  SlidersHorizontal,
  Sparkles,
  type LucideProps,
  Video,
  type LucideIcon,
} from "lucide-react";

const HighDefinitionIcon: LucideIcon = forwardRef<SVGSVGElement, LucideProps>(
  ({ color = "currentColor", size = 24, strokeWidth = 2, ...props }, ref) => createElement(
    "svg",
    {
      ref,
      width: size,
      height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: color,
      strokeLinecap: "round",
      strokeLinejoin: "round",
      strokeWidth,
      "aria-hidden": "true",
      ...props,
    },
    createElement("path", { d: "M3 7v10" }),
    createElement("path", { d: "M9 7v10" }),
    createElement("path", { d: "M3 12h6" }),
    createElement("path", { d: "M13 7v10" }),
    createElement("path", { d: "M13 7h3.2c2.7 0 4.8 2.1 4.8 5s-2.1 5-4.8 5H13" }),
  ),
);
HighDefinitionIcon.displayName = "HighDefinitionIcon";

export const WORKFLOW_CATEGORY_OPTIONS = [
  "Txt2img",
  "Img2img",
  "txt2audio",
  "audio2audio",
  "txt2vid",
  "img2vid",
  "imgTo3D",
  "txtTo3D",
  "txt2txt",
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
  model3d: Box,
  audio: FileAudio,
  text: FileText,
  maximize: Maximize2,
  highDefinition: HighDefinitionIcon,
  upscale: Scaling,
  editing: Brush,
  sliders: SlidersHorizontal,
  package: PackageOpen,
};

export const NATIVE_WORKFLOW_ICON_OPTIONS = [
  { id: "sparkles", label: "Sparkles", Icon: Sparkles },
  { id: "image", label: "Image", Icon: Image },
  { id: "video", label: "Video", Icon: Video },
  { id: "model3d", label: "3D model", Icon: Box },
  { id: "audio", label: "Audio", Icon: FileAudio },
  { id: "text", label: "Text", Icon: FileText },
  { id: "maximize", label: "Outpainting", Icon: Maximize2 },
  { id: "highDefinition", label: "High definition", Icon: HighDefinitionIcon },
  { id: "upscale", label: "Upscale", Icon: Scaling },
  { id: "editing", label: "Editing", Icon: Brush },
  { id: "sliders", label: "Controls", Icon: SlidersHorizontal },
  { id: "package", label: "Package", Icon: PackageOpen },
] as const;
